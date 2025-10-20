"""Tests for IdentifyTargetRef behaviour."""

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from py_trees.common import Status

from redis_release.bht.behaviours import IdentifyTargetRef
from redis_release.bht.state import PackageMeta, PackageMetaEphemeral, ReleaseMeta
from redis_release.github_client_async import GitHubClientAsync


@pytest.fixture
def github_client() -> MagicMock:
    """Create a mock GitHub client."""
    client = MagicMock(spec=GitHubClientAsync)
    return client


@pytest.fixture
def package_meta() -> PackageMeta:
    """Create a package meta object."""
    return PackageMeta(
        repo="redis/docker-library-redis",
        ref=None,
        publish_internal_release=False,
        ephemeral=PackageMetaEphemeral(),
    )


@pytest.fixture
def release_meta() -> ReleaseMeta:
    """Create a release meta object."""
    return ReleaseMeta(tag="8.2.1")


@pytest.mark.asyncio
async def test_identify_target_ref_already_set(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test that if ref is already set, behaviour returns SUCCESS immediately."""
    package_meta.ref = "release/8.2"

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize should do nothing
    behaviour.initialise()

    # Update should return SUCCESS immediately
    status = behaviour.update()
    assert status == Status.SUCCESS
    assert package_meta.ref == "release/8.2"

    # GitHub client should not be called
    github_client.list_remote_branches.assert_not_called()


@pytest.mark.asyncio
async def test_identify_target_ref_exact_match(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test identifying target ref with exact version match."""
    # Mock branch listing
    branches = ["release/7.2", "release/8.0", "release/8.2", "release/8.4"]

    github_client.list_remote_branches = AsyncMock(return_value=branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
    behaviour.initialise()

    # Wait for async task to complete
    await asyncio.sleep(0.1)

    # Update should detect release/8.2
    status = behaviour.update()
    assert status == Status.SUCCESS
    assert package_meta.ref == "release/8.2"
    assert behaviour.feedback_message == "Target ref set to release/8.2"


@pytest.mark.asyncio
async def test_identify_target_ref_lower_version(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test identifying target ref when exact match doesn't exist, use lower version."""
    release_meta.tag = "8.3.0"

    # Mock branch listing - no release/8.3 branch
    branches = ["release/7.2", "release/8.0", "release/8.2", "release/8.4"]

    github_client.list_remote_branches = AsyncMock(return_value=branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
    behaviour.initialise()

    # Wait for async task to complete
    await asyncio.sleep(0.1)

    # Update should detect release/8.2 (highest version <= 8.3)
    status = behaviour.update()
    assert status == Status.SUCCESS
    assert package_meta.ref == "release/8.2"


@pytest.mark.asyncio
async def test_identify_target_ref_milestone_version(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test identifying target ref for milestone version."""
    release_meta.tag = "8.4-m01"

    # Mock branch listing
    branches = ["release/7.2", "release/8.0", "release/8.2", "release/8.4"]

    github_client.list_remote_branches = AsyncMock(return_value=branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
    behaviour.initialise()

    # Wait for async task to complete
    await asyncio.sleep(0.1)

    # Update should detect release/8.4
    status = behaviour.update()
    assert status == Status.SUCCESS
    assert package_meta.ref == "release/8.4"


@pytest.mark.asyncio
async def test_identify_target_ref_no_suitable_branch(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test when no suitable branch is found (version too old)."""
    release_meta.tag = "7.0.0"

    # Mock branch listing - all branches are newer
    branches = ["release/7.2", "release/8.0", "release/8.2", "release/8.4"]

    github_client.list_remote_branches = AsyncMock(return_value=branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
    behaviour.initialise()

    # Wait for async task to complete
    await asyncio.sleep(0.1)

    # Update should fail
    status = behaviour.update()
    assert status == Status.FAILURE
    assert package_meta.ref is None
    assert behaviour.feedback_message == "Failed to detect appropriate branch"


@pytest.mark.asyncio
async def test_identify_target_ref_no_release_branches(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test when no release branches match the pattern."""
    # Mock branch listing - no release branches
    branches = ["main", "develop", "feature/test"]

    github_client.list_remote_branches = AsyncMock(return_value=branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
    behaviour.initialise()

    # Wait for async task to complete
    await asyncio.sleep(0.1)

    # Update should fail
    status = behaviour.update()
    assert status == Status.FAILURE
    assert package_meta.ref is None


@pytest.mark.asyncio
async def test_identify_target_ref_invalid_tag(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test with invalid release tag."""
    release_meta.tag = "invalid-tag"

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize should handle error gracefully
    behaviour.initialise()

    # Update should fail because task is None
    status = behaviour.update()
    assert status == Status.FAILURE


@pytest.mark.asyncio
async def test_identify_target_ref_no_tag(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test when release tag is not set."""
    release_meta.tag = None

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize should handle missing tag
    behaviour.initialise()

    # Update should fail
    status = behaviour.update()
    assert status == Status.FAILURE


@pytest.mark.asyncio
async def test_detect_branch_sorting(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test that branches are sorted correctly and highest suitable version is selected."""
    release_meta.tag = "8.5.0"

    # Mock branch listing - unsorted
    branches = ["release/8.0", "release/8.4", "release/7.2", "release/8.2"]

    github_client.list_remote_branches = AsyncMock(return_value=branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
    behaviour.initialise()

    # Wait for async task to complete
    await asyncio.sleep(0.1)

    # Update should detect release/8.4 (highest version <= 8.5)
    status = behaviour.update()
    assert status == Status.SUCCESS
    assert package_meta.ref == "release/8.4"


@pytest.mark.asyncio
async def test_identify_target_ref_running_state(
    github_client: MagicMock, package_meta: PackageMeta, release_meta: ReleaseMeta
) -> None:
    """Test that behaviour returns RUNNING while task is not complete."""
    # Create a future that won't complete immediately
    future: asyncio.Future[None] = asyncio.Future()

    async def mock_list_branches(*args: Any, **kwargs: Any) -> List[str]:
        await future
        return ["release/8.2"]

    github_client.list_remote_branches = AsyncMock(side_effect=mock_list_branches)

    behaviour = IdentifyTargetRef(
        "Test Identify Ref",
        package_meta,
        release_meta,
        github_client,
    )

    # Initialize
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
    assert package_meta.ref == "release/8.2"
