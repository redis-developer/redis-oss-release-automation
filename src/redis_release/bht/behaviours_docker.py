from typing import Optional

from py_trees.common import Status

from ..models import RedisVersion, ReleaseType
from .behaviours import LoggingAction, ReleaseAction
from .state import DockerMeta, ReleaseMeta, Workflow


class DockerWorkflowInputs(ReleaseAction):
    def __init__(
        self,
        name: str,
        workflow: Workflow,
        package_meta: DockerMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.workflow = workflow
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.package_meta.module_versions is not None:
            for module, version in self.package_meta.module_versions.items():
                self.workflow.inputs[f"{module.value}_version"] = version
        return Status.SUCCESS


class DetectReleaseTypeDocker(LoggingAction):
    """Detect release type for Docker packages based on version."""

    def __init__(
        self,
        name: str,
        package_meta: DockerMeta,
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
        try:
            self.release_version = RedisVersion.parse(self.release_meta.tag)
        except ValueError as e:
            if self.release_meta.tag != "":
                self.logger.info(
                    f"Failed to parse release tag: {e}, assuming custom release with tag {self.release_meta.tag}"
                )
            return

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
            self.package_meta.release_type = ReleaseType.INTERNAL
            self.feedback_message = "Set release type to internal for custom build"
            result = Status.SUCCESS

        if self.log_once(
            "release_type_detected", self.package_meta.ephemeral.log_once_flags
        ):
            if result == Status.SUCCESS:
                self.logger.info(f"[green]{self.feedback_message}[/green]")
            else:
                self.logger.error(f"[red]{self.feedback_message}[/red]")
        return result


# Conditions


class NeedToReleaseDocker(LoggingAction):
    """Check if Docker package needs to be released."""

    def __init__(
        self,
        name: str,
        package_meta: DockerMeta,
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
        else:
            self.feedback_message = "Custom build, need to release"
            result = Status.SUCCESS

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            color_open = "" if result == Status.SUCCESS else "[yellow]"
            color_close = "" if result == Status.SUCCESS else "[/]"
            self.logger.info(f"{color_open}{self.feedback_message}{color_close}")
        return result
