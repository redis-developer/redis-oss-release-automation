from typing import Dict, Union, cast

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from ..github_client_async import GitHubClientAsync
from .behaviours_clientimage import (
    AwaitDockerImage,
    ClientImageWorkflowInputs,
    DetectReleaseTypeClientImage,
    LocateDockerImage,
    NeedToReleaseClientImage,
)
from .composites import ResetPackageStateGuarded
from .decorators import StatusFlagGuard
from .state import ClientImageMeta, Package, PackageMeta, ReleaseMeta, Workflow
from .tree_factory_generic import GenericPackageFactory, PackageWithValidation


class ClientImageFactory(GenericPackageFactory, PackageWithValidation):
    def create_package_release_goal_tree_branch(
        self,
        packages: Dict[str, Package],
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence, Behaviour]:
        package: Package = packages[package_name]

        if "docker" not in packages:
            raise ValueError("Docker package not found in packages")

        package_release = self.create_package_release_execute_workflows_tree_branch(
            package, release_meta, default_package, github_client, package_name
        )
        need_to_release = self.create_need_to_release_behaviour(
            f"Need To Release {package_name}?",
            package.meta,
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
        package_sequence = Sequence(
            f"Release {package_name}",
            memory=False,
            children=[
                reset_package_state,
                AwaitDockerImage(
                    "Await Docker Image",
                    cast(ClientImageMeta, package.meta),
                    release_meta,
                    packages["docker"].meta,
                    packages["docker"].build,
                    log_prefix=package_name,
                ),
                LocateDockerImage(
                    "Locate Docker Image",
                    cast(ClientImageMeta, package.meta),
                    packages["docker"].build,
                    log_prefix=package_name,
                ),
                release_goal,
            ],
        )

        return StatusFlagGuard(
            name=None,
            child=package_sequence,
            container=package.meta.ephemeral,
            flag="root_node_status",
            guard_status=None,
        )

    def create_need_to_release_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return NeedToReleaseClientImage(
            name, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_detect_release_type_behaviour(
        self,
        name: str,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return DetectReleaseTypeClientImage(
            name, package_meta, release_meta, log_prefix=log_prefix
        )

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour:
        return ClientImageWorkflowInputs(
            name,
            cast(ClientImageMeta, package_meta),
            workflow,
            log_prefix=log_prefix,
        )
