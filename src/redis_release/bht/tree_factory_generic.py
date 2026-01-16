from abc import ABC
from typing import Dict, List, Optional, Union

from py_trees.behaviour import Behaviour
from py_trees.behaviours import Failure as AlwaysFailure
from py_trees.behaviours import Success as AlwaysSuccess
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from redis_release.bht.backchain import create_PPA, latch_chains
from redis_release.bht.behaviours import (
    GenericWorkflowInputs,
    IsTargetRefIdentified,
    NeedToPublishRelease,
)
from redis_release.bht.composites import (
    IdentifyTargetRefGuarded,
    ResetPackageStateGuarded,
    RestartPackageGuarded,
    RestartWorkflowGuarded,
)
from redis_release.bht.decorators import StatusFlagGuard
from redis_release.bht.ppas import (
    create_attach_release_handle_ppa,
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
)
from redis_release.bht.state import Package, PackageMeta, ReleaseMeta, Workflow
from redis_release.bht.tree_factory_protocol import GenericPackageFactoryProtocol
from redis_release.github_client_async import GitHubClientAsync

from .decorators import StatusFlagGuard


class GenericPackageFactory(ABC):
    """Default factory for packages without specific customizations."""

    def create_package_release_goal_tree_branch(
        self,
        packages: Dict[str, Package],
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence, Behaviour]:
        package: Package = packages[package_name]
        package_release = self.create_package_release_execute_workflows_tree_branch(
            package, release_meta, default_package, github_client, package_name
        )
        return StatusFlagGuard(
            name=None,
            child=Selector(
                f"Package Release {package_name} Goal",
                memory=False,
                children=[
                    Inverter(
                        "Not",
                        self.create_need_to_release_behaviour(
                            f"Need To Release {package_name}?",
                            package.meta,
                            release_meta,
                            log_prefix=package_name,
                        ),
                    ),
                    package_release,
                ],
            ),
            container=package.meta.ephemeral,
            flag="root_node_status",
            guard_status=None,
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
        identify_target_ref = self.create_identify_target_ref_tree_branch(
            package_meta,
            release_meta,
            github_client,
            log_prefix,
        )

        detect_release_type = create_PPA(
            "Detect Release Type",
            self.create_detect_release_type_behaviour(
                f"Detect Release Type",
                package_meta,
                release_meta,
                log_prefix=log_prefix,
            ),
        )

        latch_chains(
            workflow_complete,
            find_workflow_by_uud,
            trigger_workflow,
            detect_release_type,
            identify_target_ref,
        )
        return workflow_complete

    def create_package_release_execute_workflows_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        children: List[Behaviour] = []
        build = self.create_build_workflow_tree_branch(
            package,
            release_meta,
            default_package,
            github_client,
            package_name,
        )
        build.name = f"Build {package_name}"
        children.append(build)

        if package.publish is not None:
            assert default_package.publish is not None
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
            children.append(publish)

        reset_package_state = ResetPackageStateGuarded(
            "",
            package,
            default_package,
            log_prefix=package_name,
        )
        children.insert(0, reset_package_state)
        package_release = Sequence(
            f"Package Release {package_name}",
            memory=False,
            children=children,
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

    def create_need_to_release_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        """Create a behaviour that checks if the package needs to be released.

        Default implementation always returns SUCCESS (always release).
        Override in subclasses for package-specific logic.
        """
        return AlwaysSuccess(name)

    def create_detect_release_type_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        raise NotImplementedError

    def create_identify_target_ref_tree_branch(
        self,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
    ) -> Union[Selector, Sequence]:
        return create_PPA(
            "Identify Target Ref",
            IdentifyTargetRefGuarded(
                "",
                package_meta,
                release_meta,
                github_client,
                log_prefix=log_prefix,
            ),
            IsTargetRefIdentified(
                "Is Target Ref Identified?", package_meta, log_prefix=log_prefix
            ),
        )


class PackageWithValidation:
    """
    Mixin class for packages that have validation step before release, e.g. Homebrew and Snap
    """

    def create_package_release_execute_workflows_tree_branch(
        self: GenericPackageFactoryProtocol,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        children: List[Behaviour] = []
        build = self.create_build_workflow_tree_branch(
            package,
            release_meta,
            default_package,
            github_client,
            package_name,
        )
        build.name = f"Build {package_name}"
        children.append(build)
        if package.publish is not None:
            assert default_package.publish is not None
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
            children.append(publish)

        package_release = Sequence(
            f"Execute Workflows {package_name}",
            memory=False,
            children=children,
        )
        return package_release

    def create_workflow_complete_tree_branch(
        self: GenericPackageFactoryProtocol,
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
