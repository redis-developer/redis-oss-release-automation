import asyncio
import logging
import os
from typing import Tuple

import py_trees
from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from ..config import Config
from ..github_client_async import GitHubClientAsync
from .args import ReleaseArgs
from .behaviours import IsWorkflowSuccessful
from .composites import (
    FindWorkflowByUUID,
    TriggerWorkflowGoal,
    WaitForWorkflowCompletion,
)
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
    workflow = package.build.workflow
    package_meta = package.meta
    release_meta = state.meta
    logger.debug("bedaa %s", state)

    root = Sequence("Workflow Goal", False)
    workflow_run = Selector("Workflow Run", False)

    is_workflow_successful = IsWorkflowSuccessful("Is Workflow Successful?", workflow)
    identify_workflow = FindWorkflowByUUID(
        "Identify Workflow Goal", workflow, package_meta, github_client, "DOCKER"
    )
    trigger_workflow = TriggerWorkflowGoal(
        "Trigger Workflow Goal",
        workflow,
        package_meta,
        release_meta,
        github_client,
        "DOCKER",
    )
    wait_for_completion = WaitForWorkflowCompletion(
        "Workflow Completion Goal", workflow, package_meta, github_client, "DOCKER"
    )
    workflow_run.add_children(
        [
            wait_for_completion,
            identify_workflow,
            trigger_workflow,
        ]
    )
    root.add_children([workflow_run, is_workflow_successful])
    return root


async def async_tick_tock(
    tree: py_trees.trees.BehaviourTree, state_syncer: StateSyncer, period: float = 3.0
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
    while True:
        state_syncer.sync()
        print(
            py_trees.display.unicode_tree(
                tree.root, show_status=True, show_only_visited=False
            )
        )
        # TODO remove this sleep, since we are awaiting other_tasks
        await asyncio.sleep(0)
        other_tasks = asyncio.all_tasks() - {asyncio.current_task()}
        logger.debug(other_tasks)
        if not other_tasks:
            count_no_tasks_loop += 1
            # tick the tree one more time in case flipped status would lead to new tasks
            if count_no_tasks_loop > 1:
                logger.info(f"Tree finished with {tree.root.status}")
                break
        else:
            count_no_tasks_loop = 0
            await asyncio.wait(other_tasks, return_when=asyncio.FIRST_COMPLETED)

        logger.info("tick")
        tree.tick()
