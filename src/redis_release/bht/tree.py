import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Set, Tuple, Union

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter
from py_trees.display import unicode_tree
from py_trees.trees import BehaviourTree
from py_trees.visitors import SnapshotVisitor
from rich.pretty import pretty_repr
from rich.text import Text

from ..config import Config
from ..github_client_async import GitHubClientAsync
from .args import ReleaseArgs
from .backchain import latch_chains
from .behaviours import NeedToPublish
from .composites import (
    ParallelBarrier,
    ResetPackageStateGuarded,
    RestartPackageGuarded,
    RestartWorkflowGuarded,
)
from .ppas import (
    create_attach_release_handle_ppa,
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_identify_target_ref_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
    create_workflow_success_ppa,
)
from .state import (
    Package,
    PackageMeta,
    ReleaseMeta,
    ReleaseState,
    S3StateStorage,
    StateStorage,
    StateSyncer,
    Workflow,
)

logger = logging.getLogger(__name__)


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
) -> Iterator[Tuple[BehaviourTree, StateSyncer]]:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN"))

    if storage is None:
        storage = S3StateStorage()

    # Create state syncer with storage backend and acquire lock
    with StateSyncer(
        storage=storage,
        config=config,
        args=args,
    ) as state_syncer:
        root = create_root_node(
            state_syncer.state, state_syncer.default_state(), github_client
        )
        tree = BehaviourTree(root)

        # Add snapshot visitor to track visited nodes
        snapshot_visitor = SnapshotVisitor()
        tree.visitors.append(snapshot_visitor)

        tree.add_post_tick_handler(lambda _: state_syncer.sync())
        tree.add_post_tick_handler(log_tree_state_with_markup)

        yield (tree, state_syncer)


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
    state: ReleaseState, default_state: ReleaseState, github_client: GitHubClientAsync
) -> Behaviour:

    root = ParallelBarrier(
        "Redis Release",
        children=[],
    )
    for package_name, package in state.packages.items():
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

    build_workflow = create_workflow_with_result_tree_branch(
        "release_handle",
        package.build,
        package.meta,
        release_meta,
        github_client,
        f"{package_name}.build",
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
    workflow_result = create_workflow_with_result_tree_branch(
        "release_info",
        publish_workflow,
        package_meta,
        release_meta,
        github_client,
        f"{package_name}.publish",
    )
    attach_release_handle = create_attach_release_handle_ppa(
        build_workflow, publish_workflow, log_prefix=f"{package_name}.publish"
    )
    latch_chains(workflow_result, attach_release_handle)

    not_need_to_publish = Inverter(
        "Not",
        NeedToPublish(
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
) -> Union[Selector, Sequence]:
    """
    Creates a workflow process that succedes when the workflow
    is successful and a result artifact is extracted and json decoded.
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
    )

    latch_chains(workflow_result, workflow_complete)

    return workflow_result


def create_workflow_complete_tree_branch(
    workflow: Workflow,
    package_meta: PackageMeta,
    release_meta: ReleaseMeta,
    github_client: GitHubClientAsync,
    log_prefix: str,
) -> Union[Selector, Sequence]:
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
    identify_target_ref = create_identify_target_ref_ppa(
        package_meta,
        release_meta,
        github_client,
        log_prefix,
    )
    latch_chains(
        workflow_complete,
        find_workflow_by_uud,
        trigger_workflow,
        identify_target_ref,
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
