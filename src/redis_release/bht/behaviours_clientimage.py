"""Behaviours specific to client image packages."""

from py_trees.common import Status

from ..models import ReleaseType
from .behaviours import LoggingAction
from .state import PackageMeta, ReleaseMeta, Workflow


class DetectReleaseTypeClientImage(LoggingAction):
    """Detect release type for client image packages.

    Client image packages are always INTERNAL releases.
    """

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.release_meta = release_meta
        self.package_meta = package_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        # Client image packages are always internal releases
        if self.package_meta.release_type is not None:
            return Status.SUCCESS

        self.package_meta.release_type = ReleaseType.INTERNAL
        self.feedback_message = "release type is INTERNAL"

        return Status.SUCCESS


class NeedToReleaseClientImage(LoggingAction):
    """Check if client image package needs to be released.

    Client image packages always need to be released.
    """

    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        feedback_message = "Need to release client image"

        if self.log_once("need_to_release", self.package_meta.ephemeral.log_once_flags):
            self.logger.info(feedback_message)

        return Status.SUCCESS


class AwaitDockerImage(LoggingAction):
    def __init__(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        docker_build_workflow: Workflow,
        log_prefix: str = "",
    ) -> None:
        self.package_meta = package_meta
        self.release_meta = release_meta
        self.docker_build_workflow = docker_build_workflow
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.docker_build_workflow.result is not None:
            return Status.SUCCESS
        return Status.FAILURE
