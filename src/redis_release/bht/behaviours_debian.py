from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction
from redis_release.bht.state import PackageMeta, ReleaseMeta
from redis_release.models import RedisVersion, ReleaseType


class DetectReleaseTypeDebian(LoggingAction):
    """Detect release type for Debian packages based on version."""

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
            self.feedback_message = f"Release type for debian (from state): {self.package_meta.release_type}"
        elif self.release_version is not None:
            # Debian only publishes GA versions
            if self.release_version.is_ga:
                self.package_meta.release_type = ReleaseType.PUBLIC
            else:
                self.package_meta.release_type = ReleaseType.INTERNAL
            result = Status.SUCCESS
            self.feedback_message = (
                f"Detected release type for debian: {self.package_meta.release_type}"
            )
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


# Conditions


class NeedToReleaseDebian(LoggingAction):
    """Check if Debian package needs to be released."""

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
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
        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
        except ValueError as e:
            self.logger.error(f"Failed to parse release tag: {e}")
            return
        pass

    def update(self) -> Status:
        result: Status = Status.FAILURE
        if self.release_meta.tag is None:
            self.feedback_message = "Release tag is not set"
            result = Status.FAILURE
        if self.release_meta.tag == "unstable":
            self.feedback_message = "No, skip unstable release for debian"
            result = Status.FAILURE

        if self.release_version is not None:
            self.feedback_message = "Yes, need to release debian"
            result = Status.SUCCESS

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            self.logger.info(self.feedback_message)
        return result
