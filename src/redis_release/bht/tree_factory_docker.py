from typing import Union, cast

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence

from ..github_client_async import GitHubClientAsync
from .behaviours import IsTargetRefIdentified
from .behaviours_docker import (
    DetectReleaseTypeDocker,
    DockerWorkflowInputs,
    IdentifyTargetRefDocker,
    NeedToReleaseDocker,
)
from .composites import IdentifyTargetRefGuarded
from .ppas import create_PPA
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

    def create_identify_target_ref_tree_branch(
        self,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
    ) -> Union[Selector, Sequence]:
        identifier = IdentifyTargetRefDocker(
            "Identify Target Ref Docker",
            package_meta,
            release_meta,
            github_client,
            log_prefix=log_prefix,
        )
        return create_PPA(
            "Identify Target Ref",
            IdentifyTargetRefGuarded(
                "",
                package_meta,
                release_meta,
                github_client,
                log_prefix=log_prefix,
                behaviour=identifier,
            ),
            IsTargetRefIdentified(
                "Is Target Ref Identified?", package_meta, log_prefix=log_prefix
            ),
        )
