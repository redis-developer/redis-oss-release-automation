from typing import List, Optional, Protocol, Union

from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence

from redis_release.bht.state import Package, PackageMeta, ReleaseMeta, Workflow
from redis_release.github_client_async import GitHubClientAsync


class GenericPackageFactoryProtocol(Protocol):
    """Protocol defining the interface for package-specific tree factories."""

    def create_package_release_goal_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]: ...

    def create_build_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour: ...

    def create_publish_workflow_inputs(
        self,
        name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        log_prefix: str,
    ) -> Behaviour: ...

    def create_workflow_complete_tree_branch(
        self,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
        trigger_preconditions: Optional[List[Union[Sequence, Selector]]] = None,
    ) -> Union[Selector, Sequence]: ...

    def create_package_release_execute_workflows_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]: ...

    def create_build_workflow_tree_branch(
        self,
        package: Package,
        release_meta: ReleaseMeta,
        default_package: Package,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]: ...

    def create_publish_workflow_tree_branch(
        self,
        build_workflow: Workflow,
        publish_workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        default_publish_workflow: Workflow,
        github_client: GitHubClientAsync,
        package_name: str,
    ) -> Union[Selector, Sequence]: ...

    def create_workflow_with_result_tree_branch(
        self,
        artifact_name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        release_meta: ReleaseMeta,
        github_client: GitHubClientAsync,
        package_name: str,
        trigger_preconditions: Optional[List[Union[Sequence, Selector]]] = None,
    ) -> Union[Selector, Sequence]: ...

    def create_extract_result_tree_branch(
        self,
        artifact_name: str,
        workflow: Workflow,
        package_meta: PackageMeta,
        github_client: GitHubClientAsync,
        log_prefix: str,
    ) -> Union[Selector, Sequence]: ...
