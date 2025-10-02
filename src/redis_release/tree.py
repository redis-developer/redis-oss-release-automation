import asyncio
import logging
import os

import py_trees
from py_trees.behaviour import Behaviour
from py_trees.composites import Selector, Sequence
from py_trees.decorators import Inverter

from .bht.behaviours import (
    IsWorkflowSuccessful,
    IsWorkflowTriggerFailed,
    TriggerWorkflow,
)
from .bht.composites import FindWorkflowByUUID, WaitForWorkflowCompletion
from .bht.state import Workflow
from .github_client_async import GitHubClientAsync

logger = logging.getLogger(__name__)


def create_root_node() -> Behaviour:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN"))
    root = Sequence("Workflow Goal", False)
    workflow_run = Selector("Workflow Run", False)
    workflow = Workflow(
        repo="Peter-Sh/docker-library-redis",
        workflow_file="release_build_and_test.yml",
        inputs={"release_tag": "8.5.7"},
        ref="release/8.2",
    )

    is_workflow_successful = IsWorkflowSuccessful("Is Workflow Successful?", workflow)
    identify_workflow = FindWorkflowByUUID(
        "Identify Workflow", workflow, github_client, "DOCKER"
    )
    may_start_workflow = Inverter(
        "Not",
        IsWorkflowTriggerFailed("Is Workflow Trigger Failed?", workflow),
    )

    trigger_workflow = Sequence(
        "Workflow trigger",
        True,
        [
            may_start_workflow,
            TriggerWorkflow("Trigger Workflow", workflow, github_client),
        ],
    )
    wait_for_completion = WaitForWorkflowCompletion(
        "Wait for completion", workflow, github_client, "DOCKER"
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
    tree: py_trees.trees.BehaviourTree, period: float = 3.0
) -> None:
    tree.tick()
    count_no_tasks_loop = 0
    while True:
        logger.info("tick")
        print(
            py_trees.display.unicode_tree(
                tree.root, show_status=True, show_only_visited=False
            )
        )
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
        tree.tick()
