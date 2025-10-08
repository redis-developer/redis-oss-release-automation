"""Tests for behavior tree composites."""

import asyncio
import logging
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from py_trees.common import Status
from py_trees.trees import BehaviourTree

from redis_release.bht.composites import GetResultGoal, TriggerWorkflowGoal
from redis_release.bht.state import PackageMeta, ReleaseMeta, Workflow
from redis_release.github_client_async import GitHubClientAsync

logger = logging.getLogger(__name__)


async def async_tick_tock(
    tree: BehaviourTree,
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
    tree = BehaviourTree(root=trigger_goal)
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
    assert tree.root.status == Status.FAILURE, "Tree should end in FAILURE state"


async def test_get_result_goal_with_existing_artifacts() -> None:
    """Test GetResultGoal when artifacts already exist.

    This test verifies:
    1. When artifacts exist, ExtractArtifactResult is called
    2. The result is extracted and stored in workflow.result
    3. GetWorkflowArtifactsList is not called
    """
    # Setup state
    workflow = Workflow(
        workflow_file="test.yml",
        run_id=123,
        artifacts={"test-artifact": {"id": 456}},
    )
    package_meta = PackageMeta(repo="test/repo")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.download_and_extract_json_result = AsyncMock(
        return_value={"key": "value"}
    )

    # Create the composite
    get_result_goal = GetResultGoal(
        name="Get Result Goal",
        workflow=workflow,
        artifact_name="test-artifact",
        package_meta=package_meta,
        github_client=github_client,
    )

    # Setup tree
    tree = BehaviourTree(root=get_result_goal)
    tree.setup(timeout=15)

    # Run the tree
    await async_tick_tock(tree, cutoff=10)

    # Assertions
    assert workflow.result == {"key": "value"}, "Result should be extracted"
    github_client.download_and_extract_json_result.assert_called_once()


async def test_get_result_goal_downloads_artifacts_first() -> None:
    """Test GetResultGoal downloads artifacts when they don't exist.

    This test verifies:
    1. When artifacts don't exist, GetWorkflowArtifactsList is called
    2. The artifacts list is downloaded and stored in workflow.artifacts
    """
    # Setup state
    workflow = Workflow(
        workflow_file="test.yml",
        run_id=123,
        artifacts={},  # No artifacts initially
    )
    package_meta = PackageMeta(repo="test/repo")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.get_workflow_artifacts = AsyncMock(
        return_value={"test-artifact": {"id": 456}}
    )

    # Create the composite
    get_result_goal = GetResultGoal(
        name="Get Result Goal",
        workflow=workflow,
        artifact_name="test-artifact",
        package_meta=package_meta,
        github_client=github_client,
    )

    # Setup tree
    tree = BehaviourTree(root=get_result_goal)
    tree.setup(timeout=15)

    # Run the tree
    await async_tick_tock(tree, cutoff=10)

    # Assertions
    assert workflow.artifacts == {
        "test-artifact": {"id": 456}
    }, "Artifacts should be downloaded"
    github_client.get_workflow_artifacts.assert_called_once_with("test/repo", 123)


async def test_get_result_goal_handles_download_failure() -> None:
    """Test GetResultGoal handles artifact download failure.

    This test verifies:
    1. When GetWorkflowArtifactsList fails, artifacts_download_failed flag is set
    2. The tree ends in FAILURE state
    """
    # Setup state
    workflow = Workflow(
        workflow_file="test.yml",
        run_id=123,
        artifacts={},
    )
    package_meta = PackageMeta(repo="test/repo")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.get_workflow_artifacts = AsyncMock(
        side_effect=Exception("Download failed")
    )

    # Create the composite
    get_result_goal = GetResultGoal(
        name="Get Result Goal",
        workflow=workflow,
        artifact_name="test-artifact",
        package_meta=package_meta,
        github_client=github_client,
    )

    # Setup tree
    tree = BehaviourTree(root=get_result_goal)
    tree.setup(timeout=15)

    # Run the tree
    await async_tick_tock(tree, cutoff=10)

    # Assertions
    assert tree.root.status == Status.FAILURE
    assert workflow.ephemeral.artifacts_download_failed is True
    github_client.get_workflow_artifacts.assert_called_once()


async def test_get_result_goal_handles_extract_failure() -> None:
    """Test GetResultGoal handles result extraction failure and falls back.

    This test verifies:
    1. When ExtractArtifactResult fails, extract_result_failed flag is set
    2. The Selector falls back to GetWorkflowArtifactsList
    3. Artifacts are downloaded but goal fails because no result was extracted
    """
    # Setup state
    workflow = Workflow(
        workflow_file="test.yml",
        run_id=123,
        artifacts={"test-artifact": {"id": 456}},
    )
    package_meta = PackageMeta(repo="test/repo")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.download_and_extract_json_result = AsyncMock(return_value=None)
    github_client.get_workflow_artifacts = AsyncMock(
        return_value={"test-artifact": {"id": 456}}
    )

    # Create the composite
    get_result_goal = GetResultGoal(
        name="Get Result Goal",
        workflow=workflow,
        artifact_name="test-artifact",
        package_meta=package_meta,
        github_client=github_client,
    )

    # Setup tree
    tree = BehaviourTree(root=get_result_goal)
    tree.setup(timeout=15)

    # Run the tree
    await async_tick_tock(tree, cutoff=10)

    # Assertions - Goal fails because no result was extracted (even though artifacts were downloaded)
    assert workflow.ephemeral.extract_result_failed is True
    assert workflow.artifacts == {
        "test-artifact": {"id": 456}
    }, "Artifacts should be downloaded"
    # Both methods should be called - extract fails, then download succeeds
    github_client.download_and_extract_json_result.assert_called_once()
    github_client.get_workflow_artifacts.assert_called_once()
