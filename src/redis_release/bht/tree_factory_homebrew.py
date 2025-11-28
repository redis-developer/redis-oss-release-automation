from typing import Union, cast

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from redis_release.bht.behaviours_homebrew import (
    DetectHombrewReleaseAndChannel,
    DetectReleaseTypeHomebrew,
    HomewbrewWorkflowInputs,
    NeedToReleaseHomebrew,
)
from redis_release.bht.composites import (
    ClassifyHomebrewVersionGuarded,
    ResetPackageStateGuarded,
)
from redis_release.bht.state import (
    HomebrewMeta,
    Package,
    PackageMeta,
    ReleaseMeta,
    Workflow,
)
from redis_release.bht.tree_factory_generic import (
    GenericPackageFactory,
    PackageWithValidation,
)
from redis_release.github_client_async import GitHubClientAsync


class HomebrewFactory(GenericPackageFactory, PackageWithValidation):
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

    def create_detect_release_type_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        """Homebrew packages check that release_type is already set."""
        return DetectReleaseTypeHomebrew(
            name,
            cast(HomebrewMeta, package_meta),
            release_meta,
            log_prefix=log_prefix,
        )
