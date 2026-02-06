import asyncio
import re
from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction, ReleaseAction, logger
from redis_release.bht.state import HomebrewMeta, ReleaseMeta, Workflow
from redis_release.github_client_async import GitHubClientAsync
from redis_release.models import HomebrewChannel, RedisVersion, ReleaseType


class HomewbrewWorkflowInputs(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: HomebrewMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=f"{name} - homebrew", log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.release_type is not None:
            self.workflow.inputs["release_type"] = self.package_meta.release_type.value
        if self.release_meta.tag is not None:
            self.workflow.inputs["release_tag"] = self.release_meta.tag
        if self.package_meta.homebrew_channel is not None:
            self.workflow.inputs["channel"] = self.package_meta.homebrew_channel.value
        return Status.SUCCESS


class DetectHombrewReleaseAndChannel(ReleaseAction):
    def __init__(
        self,
        name: str,
        package_meta: HomebrewMeta,
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
        if self.release_meta.tag == "unstable":
            return
        self.feedback_message = ""
        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return

    def update(self) -> Status:
        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return Status.FAILURE

        if (
            self.package_meta.homebrew_channel is not None
            and self.package_meta.release_type is not None
        ):
            return Status.SUCCESS
        else:
            if self.release_meta.tag == "unstable":
                self.feedback_message = "Skip unstable release for Homebrew"
                if self.log_once(
                    "homebrew_channel_detected",
                    self.package_meta.ephemeral.log_once_flags,
                ):
                    self.logger.info(self.feedback_message)
                return Status.SUCCESS

            if self.release_version is None and self.release_meta.tag != "":
                self.logger.info(
                    f"Release version is not set, skipping probably custom release {self.release_meta.tag}"
                )
                return Status.SUCCESS

            assert self.release_version is not None
            if self.package_meta.release_type is None:
                if self.release_version.is_internal:
                    self.package_meta.release_type = ReleaseType.INTERNAL
                else:
                    if self.release_version.is_ga:
                        self.package_meta.release_type = ReleaseType.PUBLIC
                    elif self.release_version.is_rc:
                        self.package_meta.release_type = ReleaseType.PUBLIC
                    else:
                        self.package_meta.release_type = ReleaseType.INTERNAL

            if self.package_meta.homebrew_channel is None:
                if self.release_version.is_ga:
                    self.package_meta.homebrew_channel = HomebrewChannel.STABLE
                else:
                    # RC, internal, or any other version goes to RC channel
                    self.package_meta.homebrew_channel = HomebrewChannel.RC
        self.feedback_message = f"release_type: {self.package_meta.release_type.value}, homebrew_channel: {self.package_meta.homebrew_channel.value}"

        if self.log_once(
            "homebrew_channel_detected", self.package_meta.ephemeral.log_once_flags
        ):
            self.logger.info(
                f"Hombrew release_type: {self.package_meta.release_type}, homebrew_channel: {self.package_meta.homebrew_channel}"
            )

        return Status.SUCCESS


class ClassifyHomebrewVersion(ReleaseAction):
    """Classify Homebrew version by downloading and parsing the cask file.

    This behavior downloads the appropriate Homebrew cask file (redis.rb or redis-rc.rb)
    based on the homebrew_channel, extracts the version, and compares it with the
    release tag version to determine if the version is acceptable.
    """

    def __init__(
        self,
        name: str,
        package_meta: HomebrewMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.github_client = github_client
        self.task: Optional[asyncio.Task] = None
        self.release_version: Optional[RedisVersion] = None
        self.cask_version: Optional[RedisVersion] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        """Initialize by validating inputs and starting download task."""
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return

        if self.release_meta.tag == "unstable":
            self.package_meta.ephemeral.is_version_acceptable = False
            # we need to set remote version to not None as it is a sign of successful classify step
            self.package_meta.remote_version = "unstable"
            return

        if self.release_meta.tag != "":
            self.package_meta.ephemeral.is_version_acceptable = False
            # we need to set remote version to not None as it is a sign of successful classify step
            self.package_meta.remote_version = "custom"
            return

        self.feedback_message = ""
        # Validate homebrew_channel is set
        if self.package_meta.homebrew_channel is None:
            self.logger.error("Homebrew channel is not set")
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

        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
            self.logger.debug(f"Parsed release version: {self.release_version}")
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return

        # Determine which cask file to download based on channel
        if self.package_meta.homebrew_channel == HomebrewChannel.STABLE:
            cask_file = "Casks/redis.rb"
        elif self.package_meta.homebrew_channel == HomebrewChannel.RC:
            cask_file = "Casks/redis-rc.rb"
        else:
            self.logger.error(
                f"Unknown homebrew channel: {self.package_meta.homebrew_channel}"
            )
            return

        self.logger.debug(
            f"Downloading cask file: {cask_file} from {self.package_meta.repo}@{self.package_meta.ref}"
        )

        # Start async task to download the cask file from package repo and ref
        self.task = asyncio.create_task(
            self.github_client.download_file(
                self.package_meta.repo, cask_file, self.package_meta.ref
            )
        )

    def update(self) -> Status:
        """Process downloaded cask file and classify version."""
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return Status.SUCCESS

        try:
            assert self.task is not None

            # Wait for download to complete
            if not self.task.done():
                return Status.RUNNING

            # Get the downloaded content
            cask_content = self.task.result()
            if cask_content is None:
                self.logger.error("Failed to download cask file")
                return Status.FAILURE

            # Parse version from cask file
            # Look for: version "X.Y.Z"
            version_match = re.search(
                r'^\s*version\s+"([^"]+)"', cask_content, re.MULTILINE
            )
            if not version_match:
                self.logger.error("Could not find version declaration in cask file")
                return Status.FAILURE

            version_str = version_match.group(1)
            self.logger.debug(f"Found version in cask file: {version_str}")

            # Parse the cask version
            try:
                self.cask_version = RedisVersion.parse(version_str)
                self.logger.info(
                    f"Cask version: {self.cask_version}, Release version: {self.release_version}"
                )
            except ValueError as e:
                self.logger.error(f"Failed to parse cask version '{version_str}': {e}")
                return Status.FAILURE

            # Compare versions: cask version >= release version means acceptable
            assert self.release_version is not None
            self.package_meta.remote_version = str(self.cask_version)
            log_prepend = ""
            prepend_color = "green"
            if self.release_version >= self.cask_version:
                self.package_meta.ephemeral.is_version_acceptable = True
                self.feedback_message = (
                    f"release {self.release_version} >= cask {self.cask_version}"
                )
                log_prepend = "Version acceptable: "
            else:
                self.package_meta.ephemeral.is_version_acceptable = False
                log_prepend = "Version NOT acceptable: "
                prepend_color = "yellow"
                self.feedback_message = (
                    f"release {self.release_version} < cask {self.cask_version}"
                )
            if self.log_once(
                "homebrew_version_classified",
                self.package_meta.ephemeral.log_once_flags,
            ):
                self.logger.info(
                    f"[{prepend_color}]{log_prepend}{self.feedback_message}[/]"
                )
            return Status.SUCCESS

        except Exception as e:
            return self.log_exception_and_return_failure(e)


class DetectReleaseTypeHomebrew(LoggingAction):
    """Check that release_type is set for Homebrew packages.

    Homebrew packages should have release_type set by DetectHombrewReleaseAndChannel.
    This behavior just validates that it's set and fails if not.
    """

    def __init__(
        self,
        name: str,
        package_meta: HomebrewMeta,
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
            self.logger.error("Release type is not set for Homebrew package")
            return Status.FAILURE


# Conditions


class NeedToReleaseHomebrew(LoggingAction):
    def __init__(
        self,
        name: str,
        package_meta: HomebrewMeta,
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
