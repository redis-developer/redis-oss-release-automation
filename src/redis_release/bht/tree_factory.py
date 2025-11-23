"""
Package-specific tree factories and factory functions.
"""

import logging
from abc import ABC
from typing import Dict, List, Optional, Union, cast

from py_trees.behaviour import Behaviour
from py_trees.behaviours import Failure as AlwaysFailure
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from ..github_client_async import GitHubClientAsync
from ..models import PackageType
from .backchain import create_PPA, latch_chains
from .behaviours import (
    DetectHombrewReleaseAndChannel,
    DockerWorkflowInputs,
    GenericWorkflowInputs,
    HomewbrewWorkflowInputs,
    NeedToPublishRelease,
    NeedToReleaseHomebrew,
)
from .composites import (
    ClassifyHomebrewVersionGuarded,
    ResetPackageStateGuarded,
    RestartPackageGuarded,
    RestartWorkflowGuarded,
)
from .ppas import (
    create_attach_release_handle_ppa,
    create_detect_release_type_ppa,
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_identify_target_ref_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
)
from .state import (
    HomebrewMeta,
    Package,
    PackageMeta,
    ReleaseMeta,
    ReleaseState,
    Workflow,
)

logger = logging.getLogger(__name__)


class GenericPackageFactory(ABC):
    """Default factory for packages without specific customizations."""

    def create_package_release_goal_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        package_release = self.create_package_release_tree_branch(
            package, release_meta, default_package, github_client, package_name
        )
        return Selector(
            f"Package Release {package_name} Goal",
            memory=False,
            children=[AlwaysFailure("Yes"), package_release],
        )

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return GenericWorkflowInputs(
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
        return GenericWorkflowInputs(
            name, workflow, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_workflow_complete_tree_branch(
        self,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
        trigger_preconditions: Optional[List[Union[Sequence, Selector]]] = None,
    ) -> Union[Selector, Sequence]:
        """

        Args:
            trigger_preconditions: List of preconditions to add to the workflow trigger
        """
        workflow_complete = create_workflow_completion_ppa(
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        find_workflow_by_uud = create_find_workflow_by_uuid_ppa(
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        trigger_workflow = create_trigger_workflow_ppa(
            workflow,
            package_meta,
            release_meta,
            github_client,
            log_prefix,
        )
        if trigger_preconditions:
            latch_chains(trigger_workflow, *trigger_preconditions)
        identify_target_ref = create_identify_target_ref_ppa(
            package_meta,
            release_meta,
            github_client,
            log_prefix,
        )
        detect_release_type = create_detect_release_type_ppa(
            package_meta,
            release_meta,
            log_prefix,
        )
        latch_chains(
            workflow_complete,
            find_workflow_by_uud,
            trigger_workflow,
            identify_target_ref,
            detect_release_type,
        )
        return workflow_complete

    def create_package_release_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        build = self.create_build_workflow_tree_branch(
            package,
            release_meta,
            default_package,
            github_client,
            package_name,
        )
        build.name = f"Build {package_name}"
        publish = self.create_publish_workflow_tree_branch(
            package.build,
            package.publish,
            package.meta,
            release_meta,
            default_package.publish,
            github_client,
            package_name,
        )
        reset_package_state = ResetPackageStateGuarded(
            "",
            package,
            default_package,
            log_prefix=package_name,
        )
        publish.name = f"Publish {package_name}"
        package_release = Sequence(
            f"Package Release {package_name}",
            memory=False,
            children=[reset_package_state, build, publish],
        )
        return package_release

    def create_build_workflow_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:

        build_workflow_args = create_PPA(
            "Set Build Workflow Inputs",
            self.create_build_workflow_inputs(
                "Set Build Workflow Inputs",
                package.build,
                package.meta,
                release_meta,
                log_prefix=f"{package_name}.build",
            ),
        )

        build_workflow = self.create_workflow_with_result_tree_branch(
            "release_handle",
            package.build,
            package.meta,
            release_meta,
            github_client,
            f"{package_name}.build",
            trigger_preconditions=[build_workflow_args],
        )
        assert isinstance(build_workflow, Selector)

        reset_package_state = RestartPackageGuarded(
            "BuildRestartCondition",
            package,
            package.build,
            default_package,
            log_prefix=f"{package_name}.build",
        )
        build_workflow.add_child(reset_package_state)

        return build_workflow

    def create_publish_workflow_tree_branch(
        self,
        build_workflow: Workflow,
        publish_workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        default_publish_workflow: Workflow,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        attach_release_handle = create_attach_release_handle_ppa(
            build_workflow, publish_workflow, log_prefix=f"{package_name}.publish"
        )
        publish_workflow_args = create_PPA(
            "Set Publish Workflow Inputs",
            self.create_publish_workflow_inputs(
                "Set Publish Workflow Inputs",
                publish_workflow,
                package_meta,
                release_meta,
                log_prefix=f"{package_name}.publish",
            ),
        )
        workflow_result = self.create_workflow_with_result_tree_branch(
            "release_info",
            publish_workflow,
            package_meta,
            release_meta,
            github_client,
            f"{package_name}.publish",
            trigger_preconditions=[publish_workflow_args, attach_release_handle],
        )
        not_need_to_publish = Inverter(
            "Not",
            NeedToPublishRelease(
                "Need To Publish?",
                package_meta,
                release_meta,
                log_prefix=f"{package_name}.publish",
            ),
        )
        reset_publish_workflow_state = RestartWorkflowGuarded(
            "PublishRestartCondition",
            publish_workflow,
            package_meta,
            default_publish_workflow,
            log_prefix=f"{package_name}.publish",
        )
        return Selector(
            "Publish",
            memory=False,
            children=[
                not_need_to_publish,
                workflow_result,
                reset_publish_workflow_state,
            ],
        )

    def create_workflow_with_result_tree_branch(
        self,
        artifact_name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        package_name: str,
        trigger_preconditions: Optional[List[Union[Sequence, Selector]]] = None,
    ) -> Union[Selector, Sequence]:
        """
        Creates a workflow process that succedes when the workflow
        is successful and a result artifact is extracted and json decoded.

        Args:
            trigger_preconditions: List of preconditions to add to the workflow trigger
        """
        workflow_result = self.create_extract_result_tree_branch(
            artifact_name,
            workflow,
            package_meta,
            github_client,
            package_name,
        )
        workflow_complete = self.create_workflow_complete_tree_branch(
            workflow,
            package_meta,
            release_meta,
            github_client,
            package_name,
            trigger_preconditions,
        )

        latch_chains(workflow_result, workflow_complete)

        return workflow_result

    def create_extract_result_tree_branch(
        self,
        artifact_name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
    ) -> Union[Selector, Sequence]:
        extract_artifact_result = create_extract_artifact_result_ppa(
            artifact_name,
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        download_artifacts = create_download_artifacts_ppa(
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        latch_chains(extract_artifact_result, download_artifacts)
        return extract_artifact_result


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


class DebianFactory(GenericPackageFactory):
    pass


class RPMFactory(GenericPackageFactory):
    pass


class HomebrewFactory(GenericPackageFactory):
    def create_package_release_goal_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        package_release = self.create_package_release_tree_branch(
            package, release_meta, default_package, github_client, package_name
        )
        need_to_release = NeedToReleaseHomebrew(
            "Need To Release?",
            cast(HomebrewMeta, package.meta),
            release_meta,
            log_prefix=package_name,
        )
        release_goal = Selector(
            f"Release Workflows {package_name} Goal",
            memory=False,
            children=[Inverter("Not", need_to_release), package_release],
        )
        reset_package_state = ResetPackageStateGuarded(
            "",
            package,
            default_package,
            log_prefix=package_name,
        )
        return Sequence(
            f"Release {package_name}",
            memory=False,
            children=[
                reset_package_state,
                DetectHombrewReleaseAndChannel(
                    "Detect Homebrew Channel",
                    cast(HomebrewMeta, package.meta),
                    release_meta,
                    log_prefix=package_name,
                ),
                ClassifyHomebrewVersionGuarded(
                    "",
                    cast(HomebrewMeta, package.meta),
                    release_meta,
                    github_client,
                    log_prefix=package_name,
                ),
                release_goal,
            ],
        )

    def create_package_release_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        build = self.create_build_workflow_tree_branch(
            package,
            release_meta,
            default_package,
            github_client,
            package_name,
        )
        build.name = f"Build {package_name}"
        publish = self.create_publish_workflow_tree_branch(
            package.build,
            package.publish,
            package.meta,
            release_meta,
            default_package.publish,
            github_client,
            package_name,
        )
        publish.name = f"Publish {package_name}"
        package_release = Sequence(
            f"Execute Workflows {package_name}",
            memory=False,
            children=[build, publish],
        )
        return package_release

    def create_workflow_complete_tree_branch(
        self,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
        trigger_preconditions: Optional[List[Union[Sequence, Selector]]] = None,
    ) -> Union[Selector, Sequence]:
        """

        Args:
            trigger_preconditions: List of preconditions to add to the workflow trigger
        """
        workflow_complete = create_workflow_completion_ppa(
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        find_workflow_by_uud = create_find_workflow_by_uuid_ppa(
            workflow,
            package_meta,
            github_client,
            log_prefix,
        )
        trigger_workflow = create_trigger_workflow_ppa(
            workflow,
            package_meta,
            release_meta,
            github_client,
            log_prefix,
        )
        if trigger_preconditions:
            latch_chains(trigger_workflow, *trigger_preconditions)

        latch_chains(
            workflow_complete,
            find_workflow_by_uud,
            trigger_workflow,
        )
        return workflow_complete

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:

        return HomewbrewWorkflowInputs(
            name,
            workflow,
            cast(HomebrewMeta, package_meta),
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

        return HomewbrewWorkflowInputs(
            name,
            workflow,
            cast(HomebrewMeta, package_meta),
            release_meta,
            log_prefix=log_prefix,
        )



# Factory registry
_FACTORIES: Dict[PackageType, GenericPackageFactory] = {
    PackageType.DOCKER: DockerFactory(),
    PackageType.DEBIAN: DebianFactory(),
    PackageType.RPM: RPMFactory(),
    PackageType.HOMEBREW: HomebrewFactory(),
}

_DEFAULT_FACTORY = GenericPackageFactory()


def get_factory(package_type: Optional[PackageType]) -> GenericPackageFactory:
    """Get the factory for a given package type.

    Args:
        package_type: The package type to get factory for

    Returns:
        TreeFactory instance for the given type, or default factory if not found
    """
    if package_type is None:
        return _DEFAULT_FACTORY
    return _FACTORIES.get(package_type, _DEFAULT_FACTORY)
