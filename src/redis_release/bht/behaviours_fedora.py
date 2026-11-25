from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction
from redis_release.bht.state import PackageMeta, ReleaseMeta
from redis_release.models import RedisVersion, ReleaseType

# The Fedora package sets config-rewrite-source-aware, which exists from 8.12 onward.
MIN_FEDORA_VERSION = (8, 12)


class DetectReleaseTypeFedora(LoggingAction):
    """Detect release type for Fedora packages based on version."""

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
            self.feedback_message = f"Release type for Fedora (from state): {self.package_meta.release_type}"
        elif self.release_version is not None:
            # Fedora only publishes GA versions to the public Copr project
            if self.release_version.is_ga:
                self.package_meta.release_type = ReleaseType.PUBLIC
            else:
                self.package_meta.release_type = ReleaseType.INTERNAL
            result = Status.SUCCESS
            self.feedback_message = (
                f"Detected release type for Fedora: {self.package_meta.release_type}"
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


class NeedToReleaseFedora(LoggingAction):
    """Check if Fedora package needs to be released."""

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
            self.feedback_message = "Skip unstable release for Fedora"
            result = Status.FAILURE

        if self.release_version is not None:
            if (
                self.release_version.major,
                self.release_version.minor,
            ) < MIN_FEDORA_VERSION:
                self.feedback_message = f"Skip release for Fedora {str(self.release_version)} < {MIN_FEDORA_VERSION[0]}.{MIN_FEDORA_VERSION[1]}"
                self.package_meta.ephemeral.skip_message = self.feedback_message
                result = Status.FAILURE
            else:
                self.feedback_message = (
                    f"Need to release Fedora version {str(self.release_version)}"
                )
                self.package_meta.ephemeral.skip_message = None
                result = Status.SUCCESS
        else:
            self.feedback_message = "Failed to parse release version"
            result = Status.FAILURE

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            color_open = "" if result == Status.SUCCESS else "[yellow]"
            color_close = "" if result == Status.SUCCESS else "[/]"
            self.logger.info(f"{color_open}{self.feedback_message}{color_close}")
        return result
