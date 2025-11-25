from typing import Union, cast

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from redis_release.bht.behaviours_snap import (
    DetectSnapReleaseAndRiskLevel,
    NeedToReleaseSnap,
    SnapWorkflowInputs,
)
from redis_release.bht.composites import (
    ClassifySnapVersionGuarded,
    ResetPackageStateGuarded,
)
from redis_release.bht.state import (
    Package,
    PackageMeta,
    ReleaseMeta,
    SnapMeta,
    Workflow,
)
from redis_release.bht.tree_factory_generic import (
    GenericPackageFactory,
    PackageWithValidation,
)
from redis_release.github_client_async import GitHubClientAsync


class SnapFactory(GenericPackageFactory, PackageWithValidation):
    def create_package_release_goal_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
        package_release = self.create_package_release_execute_workflows_tree_branch(
            package, release_meta, default_package, github_client, package_name
        )
        need_to_release = NeedToReleaseSnap(
            "Need To Release?",
            cast(SnapMeta, package.meta),
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
                DetectSnapReleaseAndRiskLevel(
                    "Detect Homebrew Channel",
                    cast(SnapMeta, package.meta),
                    release_meta,
                    log_prefix=package_name,
                ),
                ClassifySnapVersionGuarded(
                    "",
                    cast(SnapMeta, package.meta),
                    release_meta,
                    github_client,
                    log_prefix=package_name,
                ),
                release_goal,
            ],
        )

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:

        return SnapWorkflowInputs(
            name,
            workflow,
            cast(SnapMeta, package_meta),
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

        return SnapWorkflowInputs(
            name,
            workflow,
            cast(SnapMeta, package_meta),
            release_meta,
            log_prefix=log_prefix,
        )
