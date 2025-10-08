import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from py_trees.trees import BehaviourTree

from redis_release.bht.ppas import create_download_artifacts_ppa
from redis_release.bht.state import PackageMeta, ReleaseMeta, Workflow
from redis_release.bht.tree import async_tick_tock, log_tree_state_with_markup
from redis_release.github_client_async import GitHubClientAsync
from redis_release.models import WorkflowConclusion


async def test_download_artifacts_ppa_with_empty_artifacts() -> None:
    # Setup state
    workflow = Workflow(workflow_file="test.yml", inputs={})
    workflow.conclusion = WorkflowConclusion.SUCCESS  # Mock successful workflow
    workflow.run_id = 123
    package_meta = PackageMeta(repo="test/repo", ref="main")
    release_meta = ReleaseMeta(tag="1.0.0")
    assert workflow.artifacts is None

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.get_workflow_artifacts = AsyncMock(return_value={})

    # Create PPA
    ppa = create_download_artifacts_ppa(workflow, package_meta, github_client, "")

    tree = BehaviourTree(root=ppa)
    tree.add_post_tick_handler(log_tree_state_with_markup)

    await async_tick_tock(tree, cutoff=10)

    github_client.get_workflow_artifacts.assert_called_once()
    assert workflow.artifacts == {}


async def test_download_artifacts_ppa_with_artifacts() -> None:
    # Setup state
    workflow = Workflow(workflow_file="test.yml", inputs={})
    workflow.conclusion = WorkflowConclusion.SUCCESS  # Mock successful workflow
    workflow.run_id = 123
    package_meta = PackageMeta(repo="test/repo", ref="main")
    assert workflow.artifacts is None

    # Mock GitHub client with non-empty artifacts
    github_client = MagicMock(spec=GitHubClientAsync)
    mock_artifacts = {
        "build-artifact": {
            "id": 456,
            "archive_download_url": "https://api.github.com/repos/test/repo/actions/artifacts/456/zip",
            "created_at": "2024-01-01T00:00:00Z",
            "expires_at": "2024-01-31T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "size_in_bytes": 1024,
            "digest": "abc123",
        }
    }
    github_client.get_workflow_artifacts = AsyncMock(return_value=mock_artifacts)

    # Create PPA
    ppa = create_download_artifacts_ppa(workflow, package_meta, github_client, "")

    tree = BehaviourTree(root=ppa)
    tree.add_post_tick_handler(log_tree_state_with_markup)

    await async_tick_tock(tree, cutoff=10)

    github_client.get_workflow_artifacts.assert_called_once()
    assert workflow.artifacts == mock_artifacts


async def test_download_artifacts_ppa_not_called_when_conclusion_not_success() -> None:
    # Setup state
    workflow = Workflow(workflow_file="test.yml", inputs={})
    workflow.conclusion = WorkflowConclusion.FAILURE  # Mock failed workflow
    workflow.run_id = 123
    package_meta = PackageMeta(repo="test/repo", ref="main")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.get_workflow_artifacts.return_value = AsyncMock(return_value={})

    # Create PPA
    ppa = create_download_artifacts_ppa(workflow, package_meta, github_client, "")

    tree = BehaviourTree(root=ppa)
    tree.add_post_tick_handler(log_tree_state_with_markup)

    await async_tick_tock(tree, cutoff=10)

    # GitHub client should not be called when workflow conclusion is not SUCCESS
    github_client.get_workflow_artifacts.assert_not_called()
    assert workflow.artifacts is None


async def test_download_artifacts_ppa_not_called_when_artifacts_already_empty() -> None:
    # Setup state
    workflow = Workflow(workflow_file="test.yml", inputs={})
    workflow.conclusion = WorkflowConclusion.SUCCESS  # Mock successful workflow
    workflow.run_id = 123
    workflow.artifacts = {}  # Artifacts already set to empty dict
    package_meta = PackageMeta(repo="test/repo", ref="main")

    # Mock GitHub client
    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.get_workflow_artifacts.return_value = AsyncMock(return_value={})

    # Create PPA
    ppa = create_download_artifacts_ppa(workflow, package_meta, github_client, "")

    tree = BehaviourTree(root=ppa)
    tree.add_post_tick_handler(log_tree_state_with_markup)

    await async_tick_tock(tree, cutoff=10)

    # GitHub client should not be called when artifacts are already set (even if empty)
    github_client.get_workflow_artifacts.assert_not_called()
    assert workflow.artifacts == {}
