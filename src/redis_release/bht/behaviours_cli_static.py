import asyncio
from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction, ReleaseAction
from redis_release.bht.state import CliStaticMeta, PackageMeta, ReleaseMeta
from redis_release.github_client_async import GitHubClientAsync
from redis_release.models import RedisVersion, ReleaseType


class DetectReleaseTypeCliStatic(LoggingAction):
    """Detect release type for redis-cli-static packages based on version."""

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.release_meta = release_meta
        self.package_meta = package_meta
        self.release_version: Optional[RedisVersion] = None
        super().__init__(name=name, log_prefix=log_prefix)

    def initialise(self) -> None:
        if self.package_meta.release_type is not None:
            return
        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return
        if self.release_meta.tag == "unstable":
            return
        self.release_version = RedisVersion.parse(self.release_meta.tag)

    def update(self) -> Status:
        result: Status = Status.FAILURE

        if self.package_meta.release_type is not None:
            result = Status.SUCCESS
            self.feedback_message = f"Release type for cli-static (from state): {self.package_meta.release_type}"
        elif self.release_version is not None:
            # redis-cli-static only publishes GA versions
            if self.release_version.is_ga:
                self.package_meta.release_type = ReleaseType.PUBLIC
            else:
                self.package_meta.release_type = ReleaseType.INTERNAL
            result = Status.SUCCESS
            self.feedback_message = f"Detected release type for cli-static: {self.package_meta.release_type}"
        else:
            self.feedback_message = "Failed to detect release type"
            result = Status.FAILURE

        if self.log_once(
            "release_type_detected", self.package_meta.ephemeral.log_once_flags
        ):
            if result == Status.SUCCESS:
                self.logger.info(f"[green]{self.feedback_message}[/green]")
            else:
                self.logger.error(f"[red]{self.feedback_message}[/red]")
        return result


class ClassifyCliStaticVersion(ReleaseAction):
    """Classify redis-cli-static version against the currently released version.

    Downloads the ``.redis_version`` file from the redis-cli-static repository,
    parses the plain version string it contains, and compares it with the release
    tag version to determine whether the version is acceptable.

    The version is acceptable if: release_version >= remote_version. This prevents
    releasing a version that is older than the one already published.
    """

    def __init__(
        self,
        name: str,
        package_meta: CliStaticMeta,
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
        """Validate inputs and start the download task."""
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return

        self.feedback_message = ""

        if not self.package_meta.repo:
            self.logger.error("Package repository is not set")
            return

        if not self.package_meta.ref:
            self.logger.error("Package ref is not set")
            return

        if self.release_meta.tag is None:
            self.logger.error("Release tag is not set")
            return

        if self.release_meta.tag == "unstable":
            self.package_meta.ephemeral.is_version_acceptable = False
            self.feedback_message = "Skip unstable release for cli-static"
            self.logger.info(self.feedback_message)
            # remote_version must be set as it is a sign of successful classify step
            self.package_meta.remote_version = "unstable"
            return

        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
            self.logger.debug(f"Parsed release version: {self.release_version}")
        except ValueError:
            # Non-version ref (e.g. custom build) must not move the released version
            self.logger.info(
                f"Release tag is not a version, skipping cli-static release: {self.release_meta.tag}"
            )
            self.package_meta.ephemeral.is_version_acceptable = False
            self.package_meta.remote_version = "custom"
            return

        version_file = ".redis_version"
        self.logger.debug(
            f"Downloading version file: {version_file} from "
            f"{self.package_meta.repo}@{self.package_meta.ref}"
        )
        self.task = asyncio.create_task(
            self.github_client.download_file(
                self.package_meta.repo,
                version_file,
                self.package_meta.ref,
            )
        )

    def update(self) -> Status:
        """Process the downloaded version file and classify the version."""
        if self.package_meta.ephemeral.is_version_acceptable is not None:
            return Status.SUCCESS

        try:
            assert self.task is not None

            if not self.task.done():
                return Status.RUNNING

            versions_content = self.task.result()
            if versions_content is None:
                self.logger.error("Failed to download .redis_version file")
                return Status.FAILURE

            version_str = versions_content.strip()
            try:
                self.remote_version = RedisVersion.parse(version_str)
                self.logger.info(
                    f"Remote version: {self.remote_version}, "
                    f"Release version: {self.release_version}"
                )
            except ValueError as e:
                self.logger.error(
                    f"Failed to parse remote version '{version_str}': {e}"
                )
                return Status.FAILURE

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
                "cli_static_version_classified",
                self.package_meta.ephemeral.log_once_flags,
            ):
                self.logger.info(
                    f"[{prepend_color}]{log_prepend}{self.feedback_message}[/]"
                )
            return Status.SUCCESS

        except Exception as e:
            return self.log_exception_and_return_failure(e)


class NeedToReleaseCliStatic(LoggingAction):
    """Release redis-cli-static only if the version is newer than the released one."""

    def __init__(
        self,
        name: str,
        package_meta: CliStaticMeta,
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
