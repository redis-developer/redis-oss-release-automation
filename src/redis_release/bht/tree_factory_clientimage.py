from typing import Dict, Union

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from redis_release.bht.behaviours_clientimage import (
    AwaitDockerImage,
    DetectReleaseTypeClientImage,
    NeedToReleaseClientImage,
)

from ..github_client_async import GitHubClientAsync
from .composites import ResetPackageStateGuarded
from .state import Package, PackageMeta, ReleaseMeta
from .tree_factory_generic import GenericPackageFactory, PackageWithValidation


class ClientImageFactory(GenericPackageFactory, PackageWithValidation):
    def create_package_release_goal_tree_branch(
        self,
        packages: Dict[str, Package],
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]:
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
        return Sequence(
            f"Release {package_name}",
            memory=False,
            children=[
                reset_package_state,
                AwaitDockerImage(
                    "Await Docker Image",
                    package.meta,
                    release_meta,
                    packages["docker"].build,
                    log_prefix=package_name,
                ),
                release_goal,
            ],
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
