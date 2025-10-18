import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Set, Tuple, Union

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter
from py_trees.display import unicode_tree
from py_trees.trees import BehaviourTree
from py_trees.visitors import SnapshotVisitor
from rich.text import Text

from ..config import Config
from ..github_client_async import GitHubClientAsync
from ..state_manager import S3StateStorage, StateStorage, StateSyncer
from .args import ReleaseArgs
from .backchain import latch_chains
from .behaviours import NeedToPublishRelease
from .composites import (
    ParallelBarrier,
    ResetPackageStateGuarded,
    RestartPackageGuarded,
    RestartWorkflowGuarded,
)
from .ppas import (
    create_attach_release_handle_ppa,
    create_build_workflow_inputs_ppa,
    create_detect_release_type_ppa,
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_identify_target_ref_ppa,
    create_publish_workflow_inputs_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
    create_workflow_success_ppa,
)
from .state import (
    Package,
    PackageMeta,
    ReleaseMeta,
    ReleaseState,
    Workflow,
    print_state_table,
)

logger = logging.getLogger(__name__)


class TreeInspector:
    """Inspector for creating and inspecting behavior tree branches and PPAs."""

    # List of available branch/PPA names
    AVAILABLE_NAMES = [
        "workflow_success",
        "workflow_completion",
        "find_workflow",
        "trigger_workflow",
        "identify_target_ref",
        "download_artifacts",
        "extract_artifact_result",
        "workflow_complete_branch",
        "workflow_with_result_branch",
        "publish_workflow_branch",
        "build_workflow_branch",
        "demo_sequence",
        "demo_selector",
    ]

    def __init__(self, release_tag: str):
        """Initialize TreeInspector.

        Args:
            release_tag: Release tag for creating mock ReleaseMeta
        """
        self.release_tag = release_tag

    def get_names(self) -> List[str]:
        """Get list of available branch/PPA names.

        Returns:
            List of available names that can be passed to create_by_name()
        """
        return self.AVAILABLE_NAMES.copy()

    def create_by_name(self, name: str) -> Union[Selector, Sequence, Behaviour]:
        """Create a branch or PPA by name.

        Args:
            name: Name of the branch or PPA to create

        Returns:
            The created behavior tree branch or PPA

        Raises:
            ValueError: If the name is not found in the available branches
        """
        if name not in self.AVAILABLE_NAMES:
            available = ", ".join(self.get_names())
            raise ValueError(f"Unknown name '{name}'. Available options: {available}")

        # Create mock objects for PPA/branch creation
        workflow = Workflow(workflow_file="test.yml", inputs={})
        package_meta = PackageMeta(repo="redis/redis", ref="main")
        release_meta = ReleaseMeta(tag=self.release_tag)
        github_client = GitHubClientAsync(token="dummy")
        package = Package(
            meta=package_meta,
            build=workflow,
            publish=Workflow(workflow_file="publish.yml", inputs={}),
        )
        log_prefix = "test"

        # Create and return the requested branch/PPA
        if name == "workflow_success":
            return create_workflow_success_ppa(workflow, log_prefix)
        elif name == "workflow_completion":
            return create_workflow_completion_ppa(
                workflow, package_meta, github_client, log_prefix
            )
        elif name == "find_workflow":
            return create_find_workflow_by_uuid_ppa(
                workflow, package_meta, github_client, log_prefix
            )
        elif name == "trigger_workflow":
            return create_trigger_workflow_ppa(
                workflow, package_meta, release_meta, github_client, log_prefix
            )
        elif name == "identify_target_ref":
            return create_identify_target_ref_ppa(
                package_meta, release_meta, github_client, log_prefix
            )
        elif name == "download_artifacts":
            return create_download_artifacts_ppa(
                workflow, package_meta, github_client, log_prefix
            )
        elif name == "extract_artifact_result":
            return create_extract_artifact_result_ppa(
                "test-artifact", workflow, package_meta, github_client, log_prefix
            )
        elif name == "workflow_complete_branch":
            return create_workflow_complete_tree_branch(
                workflow, package_meta, release_meta, github_client, ""
            )
        elif name == "workflow_with_result_branch":
            return create_workflow_with_result_tree_branch(
                "artifact", workflow, package_meta, release_meta, github_client, ""
            )
        elif name == "publish_workflow_branch":
            return create_publish_workflow_tree_branch(
                workflow,
                workflow,
                package_meta,
                release_meta,
                workflow,
                github_client,
                "",
            )
        elif name == "build_workflow_branch":
            return create_build_workflow_tree_branch(
                package, release_meta, package, github_client, ""
            )
        elif name == "demo_sequence":
            return create_sequence_branch()
        else:  # name == "demo_selector"
            return create_selector_branch()


async def async_tick_tock(tree: BehaviourTree, cutoff: int = 100) -> None:
    """Drive Behaviour tree using async event loop

    The tree is always ticked once.

    Next tick happens while there is at least one task completed
    or the tree is in RUNNING state.

    """
    while True:
        tree.tick()
        if tree.count > cutoff:
            logger.error(f"The Tree has not converged, hit cutoff limit {cutoff}")
            break

        other_tasks = asyncio.all_tasks() - {asyncio.current_task()}
        _debug_log_active_tasks(other_tasks)

        if not other_tasks:
            # Let the tree continue running if it's not converged
            if tree.root.status != Status.RUNNING:
                color = "green" if tree.root.status == Status.SUCCESS else "red"
                logger.info(
                    f"[bold][white]The Tree has converged to [/white][{color}]{tree.root.status}[/{color}][/bold]"
                )
                break
        else:
            await asyncio.wait(other_tasks, return_when=asyncio.FIRST_COMPLETED)


def _debug_log_active_tasks(other_tasks: Set[asyncio.Task[Any]]) -> None:
    for task in other_tasks:
        task_name = getattr(task, "get_name", lambda: "unnamed")()
        coro_name = (
            task.get_coro().__name__
            if hasattr(task.get_coro(), "__name__")
            else str(task.get_coro())
        )
        logger.debug(f"Active task: {task_name} - {coro_name}")


@contextmanager
def initialize_tree_and_state(
    config: Config,
    args: ReleaseArgs,
    storage: Optional[StateStorage] = None,
    read_only: bool = False,
) -> Iterator[Tuple[BehaviourTree, StateSyncer]]:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN") or "")

    if storage is None:
        storage = S3StateStorage()

    # Create state syncer with storage backend and acquire lock
    with StateSyncer(
        storage=storage,
        config=config,
        args=args,
        read_only=read_only,
    ) as state_syncer:
        root = create_root_node(
            state_syncer.state,
            state_syncer.default_state(),
            github_client,
            args.only_packages,
        )
        tree = BehaviourTree(root)

        # Add snapshot visitor to track visited nodes
        snapshot_visitor = SnapshotVisitor()
        tree.visitors.append(snapshot_visitor)

        tree.add_post_tick_handler(lambda _: state_syncer.sync())
        tree.add_post_tick_handler(log_tree_state_with_markup)

        yield (tree, state_syncer)
        print_state_table(state_syncer.state)


def log_tree_state_with_markup(tree: BehaviourTree) -> None:
    # Get the snapshot visitor if it exists
    snapshot_visitor = None
    for visitor in tree.visitors:
        if isinstance(visitor, SnapshotVisitor):
            snapshot_visitor = visitor
            break

    visited = snapshot_visitor.visited if snapshot_visitor else {}
    previously_visited = snapshot_visitor.previously_visited if snapshot_visitor else {}

    rich_markup = Text.from_ansi(
        unicode_tree(
            tree.root,
            show_status=True,
            show_only_visited=True,
            visited=visited,
            previously_visited=previously_visited,
        )
    ).markup
    logger.debug(f"\n{rich_markup}")


def create_root_node(
    state: ReleaseState,
    default_state: ReleaseState,
    github_client: GitHubClientAsync,
    only_packages: Optional[List[str]] = None,
) -> Behaviour:

    root = ParallelBarrier(
        "Redis Release",
        children=[],
    )
    for package_name, package in state.packages.items():
        if only_packages and package_name not in only_packages:
            logger.info(f"Skipping package {package_name} as it's not in only_packages")
            continue
        root.add_child(
            create_package_release_tree_branch(
                package,
                state.meta,
                default_state.packages[package_name],
                github_client,
                package_name,
            )
        )
    return root


def create_package_release_tree_branch(
    package: Package,
    release_meta: ReleaseMeta,
    default_package: Package,
    github_client: GitHubClientAsync,
    package_name: str,
) -> Union[Selector, Sequence]:
    build = create_build_workflow_tree_branch(
        package,
        release_meta,
        default_package,
        github_client,
        package_name,
    )
    build.name = f"Build {package_name}"
    publish = create_publish_workflow_tree_branch(
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
    package: Package,
    release_meta: ReleaseMeta,
    default_package: Package,
    github_client: GitHubClientAsync,
    package_name: str,
) -> Union[Selector, Sequence]:

    build_workflow_args = create_build_workflow_inputs_ppa(
        package.build, package.meta, release_meta, log_prefix=f"{package_name}.build"
    )
    build_workflow = create_workflow_with_result_tree_branch(
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
        "",
        package,
        package.build,
        default_package,
        log_prefix=f"{package_name}.build",
    )
    build_workflow.add_child(reset_package_state)

    return build_workflow


def create_publish_workflow_tree_branch(
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
    publish_workflow_args = create_publish_workflow_inputs_ppa(
        publish_workflow,
        package_meta,
        release_meta,
        log_prefix=f"{package_name}.publish",
    )
    workflow_result = create_workflow_with_result_tree_branch(
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
        "",
        publish_workflow,
        package_meta,
        default_publish_workflow,
        log_prefix=f"{package_name}.publish",
    )
    return Selector(
        "Publish",
        memory=False,
        children=[not_need_to_publish, workflow_result, reset_publish_workflow_state],
    )


def create_workflow_with_result_tree_branch(
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
    workflow_result = create_extract_result_tree_branch(
        artifact_name,
        workflow,
        package_meta,
        github_client,
        package_name,
    )
    workflow_complete = create_workflow_complete_tree_branch(
        workflow,
        package_meta,
        release_meta,
        github_client,
        package_name,
        trigger_preconditions,
    )

    latch_chains(workflow_result, workflow_complete)

    return workflow_result


def create_workflow_complete_tree_branch(
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


def create_extract_result_tree_branch(
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


### Demo ###


class DemoBehaviour(Behaviour):
    def __init__(self, name: str):
        super().__init__(name=name)

    def update(self) -> Status:
        return Status.SUCCESS


def create_sequence_branch() -> Sequence:
    s = Sequence(
        name="Sequence: A && B",
        memory=False,
        children=[
            DemoBehaviour("A"),
            DemoBehaviour("B"),
        ],
    )
    return s


def create_selector_branch() -> Selector:
    s = Selector(
        name="Selector: A || B",
        memory=False,
        children=[
            DemoBehaviour("A"),
            DemoBehaviour("B"),
        ],
    )
    return s
