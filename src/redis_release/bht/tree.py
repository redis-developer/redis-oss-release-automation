"""
This module contains tree initialization and utility functions to run or inspect
the tree.
"""

import asyncio
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter
from py_trees.display import unicode_tree
from py_trees.trees import BehaviourTree
from py_trees.visitors import SnapshotVisitor
from rich.text import Text

from ..config import Config, PackageConfig, custom_build_package_names
from ..github_client_async import GitHubClientAsync
from ..models import PackageType, ReleaseArgs
from ..state_console import print_state_table
from ..state_manager import S3StateStorage, StateManager, StateStorage
from ..state_slack import SlackStatePrinter, init_slack_printer
from .composites import ParallelBarrier
from .ppas import (
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
    create_workflow_success_ppa,
)
from .state import SUPPORTED_STATE_VERSION, Package, ReleaseState
from .tree_factory import get_factory

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


def resolve_package_deps(packages: List[str], config: Config) -> List[str]:
    """Resolve package dependencies using the needs field from config.

    Args:
        packages: List of package names to resolve dependencies for
        config: Configuration containing package definitions

    Returns:
        List of all packages including their dependencies
    """
    resolved: Set[str] = set()
    to_process: List[str] = list(packages)

    while to_process:
        package = to_process.pop(0)
        if package in resolved:
            continue

        resolved.add(package)

        package_config = config.packages.get(package)
        if package_config:
            for dep in package_config.needs:
                if dep not in resolved:
                    logger.debug(f"Adding package as dependency: {dep}")
                    to_process.append(dep)

    return list(resolved)


def arrange_packages_list(
    config: Config,
    packages: Dict[str, Package],
    only_packages: List[str],
    custom_build: bool,
    available_packages: List[str] = [],
) -> List[str]:
    """Arrange and filter the list of packages to process.

    Args:
        packages: Dictionary of package names to Package objects
        only_packages: List of package names to filter to (if non-empty)
        custom_build: If True, only include packages that support custom builds

    Returns:
        Filtered list of package names to process
    """
    # Define available packages based on custom_build mode
    result: List[str] = []
    if not custom_build:
        available_packages = []
    else:
        available_packages = custom_build_package_names(config)
        if not available_packages:
            raise ValueError(
                "No available packages found in config for custom build, provide allow_custom_build: true for at least one package"
            )

    if available_packages:
        if only_packages:
            for p in list(set(only_packages) - set(available_packages)):
                logger.warning(
                    f"Package {p}: not available for custom builds, but was requested as only package"
                )
            result = list(set(available_packages) & set(only_packages))
        else:
            result = available_packages
    else:
        for package_name, package in packages.items():
            if only_packages and package_name not in only_packages:
                logger.info(f"Skipping package {package_name}: not in only_packages")
                continue
            result.append(package_name)

    if not result:
        raise ValueError("No packages left after filtering")

    result = resolve_package_deps(result, config)

    return result


@contextmanager
def initialize_tree_and_state(
    config: Config,
    args: ReleaseArgs,
    storage: Optional[StateStorage] = None,
    read_only: bool = False,
) -> Iterator[Tuple[BehaviourTree, StateManager]]:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN") or "")

    if storage is None:
        storage = S3StateStorage()

    # Create state syncer with storage backend and acquire lock
    with StateManager(
        storage=storage,
        config=config,
        args=args,
        read_only=read_only,
    ) as state_syncer:
        packages_list = arrange_packages_list(
            config=config,
            packages=state_syncer.state.packages,
            only_packages=args.only_packages,
            custom_build=args.custom_build,
        )
        root = create_root_node(
            state_syncer.state,
            state_syncer.default_state(),
            github_client,
            packages_list=packages_list,
        )
        tree = BehaviourTree(root)

        if not read_only:
            state_syncer.state.meta.ephemeral.last_started_at = datetime.now(
                tz=timezone.utc
            )

        # Add snapshot visitor to track visited nodes
        snapshot_visitor = SnapshotVisitor()
        tree.visitors.append(snapshot_visitor)

        tree.add_post_tick_handler(lambda _: state_syncer.sync())
        tree.add_post_tick_handler(log_tree_state_with_markup)

        # Initialize Slack printer if Slack args are provided
        slack_printer: Optional[SlackStatePrinter] = None
        if args.slack_args and (
            args.slack_args.bot_token or args.slack_args.channel_id
        ):
            try:
                slack_printer = init_slack_printer(
                    args.slack_args.bot_token,
                    args.slack_args.channel_id,
                    args.slack_args.thread_ts,
                    args.slack_args.reply_broadcast,
                    args.slack_args.format,
                    state=state_syncer.state if not read_only else None,
                    state_name=state_syncer.state_name,
                )
                # Capture the non-None printer in the closure
                printer = slack_printer

                def slack_tick_handler(_: BehaviourTree) -> None:
                    printer.add_status(state_syncer.state)

                tree.add_post_tick_handler(slack_tick_handler)
            except ValueError as e:
                logger.error(f"Failed to initialize Slack printer: {e}")
                slack_printer = None

        try:
            yield (tree, state_syncer)
        finally:
            if not read_only:
                state_syncer.state.meta.ephemeral.last_ended_at = datetime.now(
                    tz=timezone.utc
                )
            if slack_printer:
                slack_printer.add_status(state_syncer.state)
                slack_printer.stop()
            print_state_table(state_syncer.state)
            state_syncer.sync()


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
    packages_list: List[str],
) -> Behaviour:

    root = ParallelBarrier(
        "Redis Release",
        memory=False,
        children=[],
    )
    for package_name in packages_list:
        package = state.packages[package_name]
        root.add_child(
            get_factory(
                package.meta.package_type
            ).create_package_release_goal_tree_branch(
                state.packages,
                state.meta,
                default_state.packages[package_name],
                github_client,
                package_name,
            )
        )
    return root


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
        "package_release_branch",
        "package_release_goal_branch",
        "demo_sequence",
        "demo_selector",
    ]

    def __init__(self, release_tag: str, package_type: Optional[str] = None):
        """Initialize TreeInspector.

        Args:
            release_tag: Release tag for creating mock ReleaseMeta
        """
        self.release_tag = release_tag
        if package_type:
            self.package_type = PackageType(package_type)
        else:
            self.package_type = PackageType.DOCKER

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

        config = Config(
            version=SUPPORTED_STATE_VERSION,
            packages={
                "inspected": PackageConfig(
                    repo="test/repo",
                    package_type=self.package_type,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )
        state = ReleaseState.from_config(config)
        # Create mock objects for PPA/branch creation
        workflow = state.packages["inspected"].build
        package_meta = state.packages["inspected"].meta
        release_meta = state.meta
        github_client = GitHubClientAsync(token="dummy")
        package = state.packages["inspected"]
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
            return get_factory(
                self.package_type
            ).create_identify_target_ref_tree_branch(
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
            return get_factory(self.package_type).create_workflow_complete_tree_branch(
                workflow, package_meta, release_meta, github_client, ""
            )
        elif name == "workflow_with_result_branch":
            return get_factory(
                self.package_type
            ).create_workflow_with_result_tree_branch(
                "artifact", workflow, package_meta, release_meta, github_client, ""
            )
        elif name == "publish_workflow_branch":
            return get_factory(self.package_type).create_publish_workflow_tree_branch(
                workflow,
                workflow,
                package_meta,
                release_meta,
                workflow,
                github_client,
                "",
            )
        elif name == "build_workflow_branch":
            return get_factory(self.package_type).create_build_workflow_tree_branch(
                package, release_meta, package, github_client, ""
            )
        elif name == "package_release_branch":
            return get_factory(
                self.package_type
            ).create_package_release_execute_workflows_tree_branch(
                package, release_meta, package, github_client, ""
            )
        elif name == "package_release_goal_branch":
            return get_factory(
                self.package_type
            ).create_package_release_goal_tree_branch(
                state.packages, release_meta, package, github_client, "inspected"
            )
        elif name == "demo_sequence":
            return create_sequence_branch()
        else:  # name == "demo_selector"
            return create_selector_branch()


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
