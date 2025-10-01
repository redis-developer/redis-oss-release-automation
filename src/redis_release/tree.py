import asyncio
import logging
import os

import py_trees
from py_trees.behaviour import Behaviour
from py_trees.blackboard import Blackboard

from .bht.behaviours import IsWorkflowIdentified, RedisReleaseBehaviour, TriggerWorkflow
from .bht.composites import FindWorkflowByUUID
from .bht.state import Workflow
from .github_client_async import GitHubClientAsync
from .state_manager import BlackboardStorage

logger = logging.getLogger(__name__)


def testpt():
    root = py_trees.composites.Selector("Redis Release", False)
    childuno = RedisReleaseBehaviour("Child1")

    child1 = py_trees.behaviours.Success("Childsuc")
    # child2 = py_trees.behaviours.Failure("Child2")
    # root.add_children([child1, child2])
    root.add_children([childuno, child1])
    # res = py_trees.display.render_dot_tree(root)
    # print(py_trees.display.xhtml_tree(root, "redis_release"))
    # print(res)
    print(py_trees.display.unicode_tree(root, show_status=True))
    print(
        "sjj fsldkjf f sldkjf   s fslkdjflskdjf  sldkjflskdjfskjd                  lskdjflskdjflksdfjlkj"
    )

    return root


def testpt2() -> Behaviour:
    github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN"))
    root = py_trees.composites.Selector("Redis Release", False)
    workflow = Workflow(
        repo="Peter-Sh/docker-library-redis",
        workflow_file="release_build_and_test.yml",
        inputs={"release_tag": "8.5.7"},
        ref="release/8.2",
    )
    is_workflow_identified = IsWorkflowIdentified("Is Workflow Identified?", workflow)
    identify_workflow = FindWorkflowByUUID("Identify Workflow", workflow, github_client)
    trigger_workflow = TriggerWorkflow("Trigger Workflow", workflow, github_client)
    root.add_children([is_workflow_identified, identify_workflow, trigger_workflow])
    return root


def setup_blackboard(storage: dict):
    Blackboard.storage = storage
    Blackboard.enable_activity_stream()


def save_blackboard(bbs: BlackboardStorage):
    try:
        for a in Blackboard.activity_stream.data:
            if a.activity_type == "INITIALISED" or a.activity_type == "WRITE":
                print("saving")
                bbs.put("8.2.1", Blackboard.storage)
    finally:
        Blackboard.activity_stream.clear()


async def async_tick_tock(
    tree: py_trees.trees.BehaviourTree, period: float = 3.0
) -> None:
    # bbs = BlackboardStorage()
    # stored = bbs.get("8.2.1") or {}
    # setup_blackboard(stored)
    # print(f"Stored data: {stored}")
    # # bbs.put("8.2.1", {"test": "test"})
    print("starting tick tock")
    while True:
        tree.tick()
        print("tick")
        print(
            py_trees.display.unicode_tree(
                tree.root, show_status=True, show_only_visited=False
            )
        )
        await asyncio.sleep(period)
        # print(f"bb: {Blackboard.storage}")
        # stream = [f"{a.key}:{a.activity_type}" for a in Blackboard.activity_stream.data]
        # print(f"bbas: {stream}")
        # save_blackboard(bbs)
        # print("tock")


async def async_tick_tock2(
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
            if count_no_tasks_loop > 1:
                logger.info("Tree finished")
                break
        else:
            await asyncio.wait(other_tasks, return_when=asyncio.FIRST_COMPLETED)
        tree.tick()
