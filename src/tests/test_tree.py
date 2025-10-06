"""Tests for behavior tree composites."""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import py_trees

from redis_release.bht.composites import TriggerWorkflowGoal
from redis_release.bht.state import PackageMeta, ReleaseMeta, Workflow
from redis_release.github_client_async import GitHubClientAsync


async def async_tick_tock(
    tree: py_trees.trees.BehaviourTree,
    cutoff: int = 100,
    period: float = 0.01,
) -> None:
    """Drive Behaviour tree using async event loop with tick cutoff.

    Args:
        tree: The behavior tree to tick
        cutoff: Maximum number of ticks before stopping
        period: Time to wait between ticks (default: 0.01s)
    """
    tree.tick()
    tick_count = 1
    count_no_tasks_loop = 0

    while tick_count < cutoff:
        other_tasks = asyncio.all_tasks() - {asyncio.current_task()}

        if not other_tasks:
            count_no_tasks_loop += 1
            # tick the tree one more time in case flipped status would lead to new tasks
            if count_no_tasks_loop > 1:
                break
        else:
            count_no_tasks_loop = 0
            await asyncio.wait(other_tasks, return_when=asyncio.FIRST_COMPLETED)

        tree.tick()
        tick_count += 1
        await asyncio.sleep(period)


async def test_trigger_workflow_goal_handles_trigger_failure() -> None:
    """Test that TriggerWorkflowGoal sets trigger_failed flag when TriggerWorkflow fails.

    This test verifies:
    1. When TriggerWorkflow returns FAILURE, the trigger_failed flag is set
    2. GitHub client's trigger_workflow is called only once (not repeatedly)
    """
    # Setup state
    workflow = Workflow(workflow_file="test.yml", inputs={})
    package_meta = PackageMeta(repo="test/repo", ref="main")
    release_meta = ReleaseMeta(tag="1.0.0")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.trigger_workflow = AsyncMock(side_effect=Exception("Trigger failed"))

    # Create the composite
    trigger_goal = TriggerWorkflowGoal(
        name="Test Trigger Goal",
        workflow=workflow,
        package_meta=package_meta,
        release_meta=release_meta,
        github_client=github_client,
    )

    # Setup tree
    tree = py_trees.trees.BehaviourTree(root=trigger_goal)
    tree.setup(timeout=15)

    # Run the tree
    await async_tick_tock(tree, cutoff=10)

    # Assertions
    assert (
        workflow.ephemeral.trigger_failed is True
    ), "trigger_failed flag should be set"
    assert github_client.trigger_workflow.call_count == 1, (
        f"GitHub trigger_workflow should be called exactly once, "
        f"but was called {github_client.trigger_workflow.call_count} times"
    )
    assert (
        tree.root.status == py_trees.common.Status.FAILURE
    ), "Tree should end in FAILURE state"
