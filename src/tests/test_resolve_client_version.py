"""Tests for ResolveClientVersion behaviour."""

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from py_trees.common import Status

from redis_release.bht.behaviours_clienttest import ResolveClientVersion
from redis_release.bht.state import ClientTestMeta, ClientTestMetaEphemeral
from redis_release.github_client_async import GitHubClientAsync


@pytest.fixture
def github_client() -> MagicMock:
    """Create a mock GitHub client."""
    client = MagicMock(spec=GitHubClientAsync)
    return client


@pytest.fixture
def client_test_meta_default_pattern() -> ClientTestMeta:
    """Create a ClientTestMeta with default pattern (redis-py style: v1.2.3)."""
    meta = ClientTestMeta(repo="redis-developer/redis-oss-release-automation")
    meta.client_repo = "redis/redis-py"
    meta.client_ref = None
    meta.version_ref_pattern = r"^v(\d+)\.(\d+)\.(\d+)$"
    meta.ephemeral = ClientTestMetaEphemeral()
    return meta


@pytest.fixture
def client_test_meta_lettuce_pattern() -> ClientTestMeta:
    """Create a ClientTestMeta with lettuce pattern (1.2.3.RELEASE)."""
    meta = ClientTestMeta(repo="redis-developer/redis-oss-release-automation")
    meta.client_repo = "redis/lettuce"
    meta.client_ref = None
    meta.version_ref_pattern = r"^(\d+)\.(\d+)\.(\d+).RELEASE$"
    meta.ephemeral = ClientTestMetaEphemeral()
    return meta


# --- Tests with default pattern (redis-py style: v1.2.3) ---


@pytest.mark.asyncio
async def test_client_ref_already_set(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test that if client_ref is already set, behaviour returns SUCCESS immediately."""
    client_test_meta_default_pattern.client_ref = "v5.0.0"

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()
    status = behaviour.update()

    assert status == Status.SUCCESS
    assert client_test_meta_default_pattern.client_ref == "v5.0.0"
    github_client.list_remote_tags.assert_not_called()


@pytest.mark.asyncio
async def test_default_pattern_matches_redis_py_tags(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test with real redis-py tags using default pattern."""
    # Real tags from redis/redis-py
    tags = [
        "v6.3.0",
        "v6.4.0",
        "v7.0.0",
        "v7.0.1",
        "v7.1.0",
        "v7.1.1",
        "v7.2.0",
        "v7.2.1",
        "v7.3.0",
        "v7.4.0",
    ]

    github_client.list_remote_tags = AsyncMock(return_value=tags)

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()
    await asyncio.sleep(0.1)

    status = behaviour.update()
    assert status == Status.SUCCESS
    assert client_test_meta_default_pattern.client_ref == "v7.4.0"
    assert behaviour.feedback_message == "Client ref set to v7.4.0"


@pytest.mark.asyncio
async def test_default_pattern_no_matching_tags(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test when no tags match the default pattern."""
    # Tags that don't match ^v(\d+)\.(\d+)\.(\d+)$
    tags = [
        "v7.0.0b1",
        "v7.0.0b2",
        "v8.0.0b1",
        "release-1.0",
        "1.0.0.RELEASE",
    ]

    github_client.list_remote_tags = AsyncMock(return_value=tags)

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()
    await asyncio.sleep(0.1)

    status = behaviour.update()
    assert status == Status.FAILURE
    assert client_test_meta_default_pattern.client_ref is None
    assert behaviour.feedback_message == "No matching tags found"


# --- Tests with lettuce pattern (1.2.3.RELEASE) ---


@pytest.mark.asyncio
async def test_lettuce_pattern_matches_lettuce_tags(
    github_client: MagicMock, client_test_meta_lettuce_pattern: ClientTestMeta
) -> None:
    """Test with real lettuce tags using lettuce pattern."""
    # Real tags from redis/lettuce
    tags = [
        "7.1.0.RELEASE",
        "7.1.1.RELEASE",
        "7.2.0.RELEASE",
        "7.2.1.RELEASE",
        "7.3.0.RELEASE",
        "7.3.1.RELEASE",
        "7.4.0.RELEASE",
        "7.4.1.RELEASE",
        "7.5.0.RELEASE",
        "7.5.1.RELEASE",
    ]

    github_client.list_remote_tags = AsyncMock(return_value=tags)

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_lettuce_pattern,
        github_client,
    )

    behaviour.initialise()
    await asyncio.sleep(0.1)

    status = behaviour.update()
    assert status == Status.SUCCESS
    assert client_test_meta_lettuce_pattern.client_ref == "7.5.1.RELEASE"
    assert behaviour.feedback_message == "Client ref set to 7.5.1.RELEASE"


@pytest.mark.asyncio
async def test_lettuce_pattern_no_matching_tags(
    github_client: MagicMock, client_test_meta_lettuce_pattern: ClientTestMeta
) -> None:
    """Test when no tags match the lettuce pattern."""
    # Tags that don't match ^(\d+)\.(\d+)\.(\d+).RELEASE$
    tags = [
        "v7.0.0",
        "7.4.0.BETA1",
        "pre-io",
        "v7.0.0.BETA1",
        "snapshot-1.0.0",
    ]

    github_client.list_remote_tags = AsyncMock(return_value=tags)

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_lettuce_pattern,
        github_client,
    )

    behaviour.initialise()
    await asyncio.sleep(0.1)

    status = behaviour.update()
    assert status == Status.FAILURE
    assert client_test_meta_lettuce_pattern.client_ref is None
    assert behaviour.feedback_message == "No matching tags found"


# --- Tests for sorting ---


@pytest.mark.asyncio
async def test_tags_sorted_correctly_descending(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test that tags are sorted correctly and highest version is selected."""
    # Unsorted tags
    tags = [
        "v5.0.0",
        "v7.4.0",
        "v6.3.0",
        "v7.1.1",
        "v7.2.0",
    ]

    github_client.list_remote_tags = AsyncMock(return_value=tags)

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()
    await asyncio.sleep(0.1)

    status = behaviour.update()
    assert status == Status.SUCCESS
    assert client_test_meta_default_pattern.client_ref == "v7.4.0"


# --- Tests for edge cases ---


@pytest.mark.asyncio
async def test_client_repo_not_set(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test when client_repo is not set."""
    client_test_meta_default_pattern.client_repo = None

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()
    status = behaviour.update()

    assert status == Status.FAILURE
    github_client.list_remote_tags.assert_not_called()


@pytest.mark.asyncio
async def test_empty_tags_list(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test when tag list is empty."""
    github_client.list_remote_tags = AsyncMock(return_value=[])

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()
    await asyncio.sleep(0.1)

    status = behaviour.update()
    assert status == Status.FAILURE
    assert client_test_meta_default_pattern.client_ref is None
    assert behaviour.feedback_message == "No matching tags found"


@pytest.mark.asyncio
async def test_running_state_while_task_pending(
    github_client: MagicMock, client_test_meta_default_pattern: ClientTestMeta
) -> None:
    """Test that behaviour returns RUNNING while task is not complete."""
    future: asyncio.Future[None] = asyncio.Future()

    async def mock_list_tags(*args: Any, **kwargs: Any) -> List[str]:
        await future
        return ["v7.4.0"]

    github_client.list_remote_tags = AsyncMock(side_effect=mock_list_tags)

    behaviour = ResolveClientVersion(
        "Test Resolve Client Version",
        client_test_meta_default_pattern,
        github_client,
    )

    behaviour.initialise()

    # Update should return RUNNING
    status = behaviour.update()
    assert status == Status.RUNNING

    # Complete the future
    future.set_result(None)
    await asyncio.sleep(0.1)

    # Now update should succeed
    status = behaviour.update()
    assert status == Status.SUCCESS
    assert client_test_meta_default_pattern.client_ref == "v7.4.0"
