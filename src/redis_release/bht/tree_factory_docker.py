from py_trees.behaviour import Behaviour

from redis_release.bht.behaviours_docker import DockerWorkflowInputs
from redis_release.bht.state import PackageMeta, ReleaseMeta, Workflow
from redis_release.bht.tree_factory_generic import GenericPackageFactory


class DockerFactory(GenericPackageFactory):
    """Factory for Docker packages."""

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return DockerWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_publish_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return DockerWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )
