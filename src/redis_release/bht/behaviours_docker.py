from typing import Optional

from py_trees.common import Status

from redis_release.bht.behaviours import LoggingAction, ReleaseAction
from redis_release.bht.state import PackageMeta, ReleaseMeta, Workflow
from redis_release.models import RedisVersion, ReleaseType


class DockerWorkflowInputs(ReleaseAction):
    """
    Docker uses only release_tag input which is set automatically in TriggerWorkflow
    """

    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        return Status.SUCCESS


# Conditions


class DetectReleaseTypeDocker(LoggingAction):
    """Detect release type for Docker packages based on version."""

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
            self.feedback_message = f"Release type for docker (from state): {self.package_meta.release_type}"
        elif self.release_version is not None:
            if self.release_version.is_internal:
                self.package_meta.release_type = ReleaseType.INTERNAL
            else:
                self.package_meta.release_type = ReleaseType.PUBLIC
            result = Status.SUCCESS
            self.feedback_message = (
                f"Detected release type for docker: {self.package_meta.release_type}"
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


class NeedToReleaseDocker(LoggingAction):
    """Check if Docker package needs to be released."""

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
            self.feedback_message = "Skip unstable release for docker"
            result = Status.FAILURE

        if self.release_version is not None:
            if self.release_version.major < 8:
                self.feedback_message = (
                    f"Skip release for docker {str(self.release_version)} < 8.0"
                )
                result = Status.FAILURE
            else:
                self.feedback_message = (
                    f"Need to release docker version {str(self.release_version)}"
                )
                result = Status.SUCCESS

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            color_open = "" if result == Status.SUCCESS else "yellow"
            color_close = "" if result == Status.SUCCESS else "[/]"
            self.logger.info(f"{color_open}{self.feedback_message}{color_close}")
        return result
