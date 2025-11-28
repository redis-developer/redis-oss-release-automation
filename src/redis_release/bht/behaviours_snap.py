import asyncio
import json
from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction, ReleaseAction, logger
from redis_release.bht.state import ReleaseMeta, SnapMeta, Workflow
from redis_release.github_client_async import GitHubClientAsync
from redis_release.models import RedisVersion, ReleaseType, SnapRiskLevel


class SnapWorkflowInputs(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: SnapMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=f"{name} - snap", log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is not None:
            self.workflow.inputs["release_type"] = self.package_meta.release_type.value
        if self.release_meta.tag is not None:
            self.workflow.inputs["release_tag"] = self.release_meta.tag
        if self.package_meta.snap_risk_level is not None:
            self.workflow.inputs["risk_level"] = self.package_meta.snap_risk_level.value
        return Status.SUCCESS


class DetectSnapReleaseAndRiskLevel(ReleaseAction):
    """Detect Snap release type and risk level based on release version.

    Logic:
    - is_internal: sets release_type to INTERNAL, risk_level to CANDIDATE
    - is_ga: sets release_type to PUBLIC, risk_level to STABLE
    - is_rc: sets release_type to PUBLIC, risk_level to CANDIDATE
    """

    def __init__(
        self,
        name: str,
        package_meta: SnapMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.release_version: Optional[RedisVersion] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return
        if self.release_version is not None:
            return

        self.feedback_message = ""
        if self.release_meta.tag == "unstable":
            return
        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return

    def update(self) -> Status:
        if self.release_meta.tag is None:
            logger.error("Release tag is not set")
            return Status.FAILURE

        if (
            self.package_meta.snap_risk_level is not None
            and self.package_meta.release_type is not None
        ):
            return Status.SUCCESS
        else:
            if self.release_meta.tag == "unstable":
                self.package_meta.release_type = ReleaseType.PUBLIC
                self.package_meta.snap_risk_level = SnapRiskLevel.EDGE
            else:
                assert self.release_version is not None
                if self.package_meta.release_type is None:
                    if self.release_version.is_internal:
                        self.package_meta.release_type = ReleaseType.INTERNAL
                        self.package_meta.snap_risk_level = SnapRiskLevel.CANDIDATE
                    else:
                        self.package_meta.release_type = ReleaseType.PUBLIC

                if self.package_meta.snap_risk_level is None:
                    if self.release_version.is_ga:
                        self.package_meta.snap_risk_level = SnapRiskLevel.STABLE
                    else:
                        # other versions go to CANDIDATE
                        self.package_meta.snap_risk_level = SnapRiskLevel.CANDIDATE

        self.feedback_message = f"release_type: {self.package_meta.release_type.value}, snap_risk_level: {self.package_meta.snap_risk_level.value}"

        if self.log_once(
            "snap_risk_level_detected", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(
                f"Snap release_type: {self.package_meta.release_type}, snap_risk_level: {self.package_meta.snap_risk_level}"
            )

        return Status.SUCCESS


class ClassifySnapVersion(ReleaseAction):
    """Classify Snap version by downloading and parsing .redis_versions.json file.

    This behavior downloads the .redis_versions.json file from the snap repository,
    extracts the version for the appropriate risk level (stable/candidate/edge),
    and compares it with the release tag version to determine if the version is acceptable.

    The version is acceptable if: release_version >= remote_version for the risk level.
    """

    def __init__(
        self,
        name: str,
        package_meta: SnapMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.github_client = github_client
        self.task: Optional[asyncio.Task] = None
        self.release_version: Optional[RedisVersion] = None
        self.remote_version: Optional[RedisVersion] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        """Initialize by validating inputs and starting download task."""
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return

        self.feedback_message = ""
        # Validate snap_risk_level is set
        if self.package_meta.snap_risk_level is None:
            self.logger.error("Snap risk level is not set")
            return

        # Validate repo and ref are set
        if not self.package_meta.repo:
            self.logger.error("Package repository is not set")
            return

        if not self.package_meta.ref:
            self.logger.error("Package ref is not set")
            return

        # Parse release version from tag
        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return

        if self.package_meta.release_type is None:
            self.logger.error("Package release type is not set")
            return

        if self.package_meta.snap_risk_level is None:
            self.logger.error("Snap risk level is not set")
            return

        if self.release_meta.tag == "unstable":
            self.package_meta.ephemeral.is_version_acceptable = True
            # we need to set remote version to not None as it is a sign of successful classify step
            self.package_meta.remote_version = "unstable"
            return

        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
            self.logger.debug(f"Parsed release version: {self.release_version}")
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return

        # Download .redis_versions.json file
        versions_file = ".redis_versions.json"
        self.logger.debug(
            f"Downloading versions file: {versions_file} from {self.package_meta.repo}@{self.package_meta.ref}"
        )

        # Start async task to download the versions file from package repo and ref
        self.task = asyncio.create_task(
            self.github_client.download_file(
                self.package_meta.repo, versions_file, self.package_meta.ref
            )
        )

    def update(self) -> Status:
        """Process downloaded versions file and classify version."""
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return Status.SUCCESS

        try:
            assert self.task is not None

            # Wait for download to complete
            if not self.task.done():
                return Status.RUNNING

            # Get the downloaded content
            versions_content = self.task.result()
            if versions_content is None:
                self.logger.error("Failed to download .redis_versions.json file")
                return Status.FAILURE

            # Parse JSON content
            try:
                versions_data = json.loads(versions_content)
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse .redis_versions.json: {e}")
                return Status.FAILURE

            # Get version for the current risk level
            assert self.package_meta.snap_risk_level is not None
            risk_level_key = self.package_meta.snap_risk_level.value
            if risk_level_key not in versions_data:
                self.logger.error(
                    f"Risk level '{risk_level_key}' not found in .redis_versions.json"
                )
                return Status.FAILURE

            version_str = versions_data[risk_level_key]
            self.logger.debug(
                f"Found version for {risk_level_key} risk level: {version_str}"
            )

            try:
                self.remote_version = RedisVersion.parse(version_str)
                self.logger.info(
                    f"Remote version: {self.remote_version}, Release version: {self.release_version}"
                )
            except ValueError as e:
                self.logger.error(
                    f"Failed to parse remote version '{version_str}': {e}"
                )
                return Status.FAILURE

            # Compare versions: release version >= remote version means acceptable
            assert self.release_version is not None
            self.package_meta.remote_version = str(self.remote_version)
            log_prepend = ""
            prepend_color = "green"
            if self.release_version >= self.remote_version:
                self.package_meta.ephemeral.is_version_acceptable = True
                self.feedback_message = (
                    f"release {self.release_version} >= remote {self.remote_version}"
                )
                log_prepend = "Version acceptable: "
            else:
                self.package_meta.ephemeral.is_version_acceptable = False
                log_prepend = "Version NOT acceptable: "
                prepend_color = "yellow"
                self.feedback_message = (
                    f"release {self.release_version} < remote {self.remote_version}"
                )
            if self.log_once(
                "snap_version_classified",
                self.package_meta.ephemeral.log_once_flags,
            ):
                self.logger.info(
                    f"[{prepend_color}]{log_prepend}{self.feedback_message}[/]"
                )
            return Status.SUCCESS

        except Exception as e:
            return self.log_exception_and_return_failure(e)


class DetectReleaseTypeSnap(LoggingAction):
    """Check that release_type is set for Snap packages.

    Snap packages should have release_type set by DetectSnapReleaseAndRiskLevel.
    This behavior just validates that it's set and fails if not.
    """

    def __init__(
        self,
        name: str,
        package_meta: SnapMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is not None:
            self.feedback_message = f"Release type: {self.package_meta.release_type}"
            return Status.SUCCESS
        else:
            self.feedback_message = "Release type is not set"
            self.logger.error("Release type is not set for Snap package")
            return Status.FAILURE


class NeedToReleaseSnap(LoggingAction):
    def __init__(
        self,
        name: str,
        package_meta: SnapMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.ephemeral.is_version_acceptable is True:
            return Status.SUCCESS
        return Status.FAILURE
