import asyncio
import logging
import os
from typing import Tuple

import py_trees
from py_trees.behaviour import Behaviour

from ..config import Config
from ..github_client_async import GitHubClientAsync
from .args import ReleaseArgs
from .composites import ReleasePhaseGoal
from .state import ReleaseState, StateSyncer

logger = logging.getLogger(__name__)


def initialize_tree_and_state(
    config: Config, args: ReleaseArgs
) -> Tuple[Behaviour, StateSyncer]:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN"))
    state_syncer = StateSyncer(config, args)

    return (create_root_node(state_syncer.state, github_client), state_syncer)


def create_root_node(
    state: ReleaseState, github_client: GitHubClientAsync
) -> Behaviour:

    # Get package and workflow
    package = state.packages["docker"]
    package_meta = package.meta
    release_meta = state.meta

    # Create build phase goal
    build_phase = ReleasePhaseGoal(
        phase_name="build",
        workflow=package.build,
        artifact_name="build-result",
        package_meta=package_meta,
        release_meta=release_meta,
        github_client=github_client,
        log_prefix="DOCKER",
    )

    return build_phase


async def async_tick_tock(
    tree: py_trees.trees.BehaviourTree, state_syncer: StateSyncer, cutoff: int = 100
) -> None:
    """Drive Behaviour tree using async event loop

    The tree is always ticked once.

    Next tick happens when there is at least one task completed.
    If async tasks list is empty the final tick is made and if
    after that the async tasks queue is still empty the tree is
    considered finished.

    """
    print(
        py_trees.display.unicode_tree(
            tree.root, show_status=True, show_only_visited=False
        )
    )
    tree.tick()
    count_no_tasks_loop = 0
    count = 0
    while True:
        count += 1
        state_syncer.sync()
        if count > cutoff:
            logger.info(f"The Tree has not converged, hit cutoff limit {cutoff}")
            break
        print(
            py_trees.display.unicode_tree(
                tree.root, show_status=True, show_only_visited=False
            )
        )
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

        logger.info("tick")
        tree.tick()
