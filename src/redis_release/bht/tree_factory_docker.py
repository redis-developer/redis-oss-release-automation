from typing import cast

from py_trees.behaviour import Behaviour

from .behaviours_docker import (
    DetectReleaseTypeDocker,
    DockerWorkflowInputs,
    NeedToReleaseDocker,
)
from .state import DockerMeta, PackageMeta, ReleaseMeta, Workflow
from .tree_factory_generic import GenericPackageFactory


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
            name,
            workflow,
            cast(DockerMeta, package_meta),
            release_meta,
            log_prefix=log_prefix,
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
            name,
            workflow,
            cast(DockerMeta, package_meta),
            release_meta,
            log_prefix=log_prefix,
        )

    def create_need_to_release_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return NeedToReleaseDocker(
            name, cast(DockerMeta, package_meta), release_meta, log_prefix=log_prefix
        )

    def create_detect_release_type_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return DetectReleaseTypeDocker(
            name, cast(DockerMeta, package_meta), release_meta, log_prefix=log_prefix
        )
