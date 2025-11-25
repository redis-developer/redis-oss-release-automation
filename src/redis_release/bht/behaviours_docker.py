from py_trees.common import Status

from redis_release.bht.behaviours import ReleaseAction
from redis_release.bht.state import PackageMeta, ReleaseMeta, Workflow


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
