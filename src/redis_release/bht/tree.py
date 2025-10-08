import asyncio
import logging
import os
from typing import Tuple

from py_trees.behaviour import Behaviour
from py_trees.display import unicode_tree
from py_trees.trees import BehaviourTree
from rich.text import Text

from ..config import Config
from ..github_client_async import GitHubClientAsync
from .args import ReleaseArgs
from .backchain import latch_chains
from .ppas import (
    create_find_workflow_by_uuid_ppa,
    create_identify_target_ref_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
    create_workflow_success_ppa,
)
from .state import ReleaseState, StateSyncer

logger = logging.getLogger(__name__)


def initialize_tree_and_state(
    config: Config, args: ReleaseArgs
) -> Tuple[BehaviourTree, StateSyncer]:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN"))
    state_syncer = StateSyncer(config, args)

    root = create_root_node(state_syncer.state, github_client)
    tree = BehaviourTree(root)
    tree.add_pre_tick_handler(lambda _: state_syncer.sync())
    tree.add_post_tick_handler(log_tree_state_with_markup)

    return (tree, state_syncer)


def log_tree_state_with_markup(tree: BehaviourTree) -> None:
    rich_markup = Text.from_ansi(
        unicode_tree(tree.root, show_status=True, show_only_visited=False)
    ).markup
    logger.debug(f"\n{rich_markup}")


def create_root_node(
    state: ReleaseState, github_client: GitHubClientAsync
) -> Behaviour:

    root = create_workflow_success_tree_branch(state, github_client)

    return root


def create_workflow_success_tree_branch(
    state: ReleaseState, github_client: GitHubClientAsync
) -> Behaviour:

    workflow_success = create_workflow_success_ppa(
        state.packages["docker"].build,
        "docker",
    )
    workflow_complete = create_workflow_completion_ppa(
        state.packages["docker"].build,
        state.packages["docker"].meta,
        github_client,
        "docker",
    )
    find_workflow_by_uud = create_find_workflow_by_uuid_ppa(
        state.packages["docker"].build,
        state.packages["docker"].meta,
        github_client,
        "docker",
    )
    trigger_workflow = create_trigger_workflow_ppa(
        state.packages["docker"].build,
        state.packages["docker"].meta,
        state.meta,
        github_client,
        "docker",
    )
    identify_target_ref = create_identify_target_ref_ppa(
        state.packages["docker"].meta,
        state.meta,
        "docker",
    )
    latch_chains(
        workflow_success,
        workflow_complete,
        find_workflow_by_uud,
        trigger_workflow,
        identify_target_ref,
    )
    return workflow_success


async def async_tick_tock(tree: BehaviourTree, cutoff: int = 100) -> None:
    """Drive Behaviour tree using async event loop

    The tree is always ticked once.

    Next tick happens when there is at least one task completed.
    If async tasks list is empty the final tick is made and if
    after that the async tasks queue is still empty the tree is
    considered finished.

    """
    count_no_tasks_loop = 0
    while True:
        tree.tick()
        if tree.count > cutoff:
            logger.error(f"The Tree has not converged, hit cutoff limit {cutoff}")
            break

        other_tasks = asyncio.all_tasks() - {asyncio.current_task()}
        logger.debug(other_tasks)
        if not other_tasks:
            count_no_tasks_loop += 1
            # tick the tree one more time in case flipped status would lead to new tasks
            if count_no_tasks_loop > 1:
                logger.info(f"The Tree has converged to {tree.root.status}")
                break
        else:
            count_no_tasks_loop = 0
            await asyncio.wait(other_tasks, return_when=asyncio.FIRST_COMPLETED)
