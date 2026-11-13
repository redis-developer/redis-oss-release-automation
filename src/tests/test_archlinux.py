"""Tests for the archlinux package release decision, driven through its tree branch."""

from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from py_trees.common import Status
from py_trees.trees import BehaviourTree

from redis_release.bht.state import ArchlinuxMeta, ReleaseState
from redis_release.bht.tree import async_tick_tock
from redis_release.bht.tree_factory import get_factory
from redis_release.config import Config
from redis_release.github_client_async import GitHubClientAsync
from redis_release.models import PackageType, ReleaseType

CONFIG_PATH = Path(__file__).parents[2] / "config.yaml"
PACKAGE = "archlinux"


def srcinfo(pkgver: str) -> str:
    return f"pkgbase = redis\n\tpkgdesc = x\n\tpkgver = {pkgver}\n\tpkgrel = 1\n"


class Run(NamedTuple):
    state: ReleaseState
    dispatches: List[Dict[str, Any]]
    download_file: AsyncMock
    status: Status


async def run_release(
    tag: str,
    srcinfo_body: str,
    forced_release_type: Optional[ReleaseType] = None,
    slack_channel_id: Optional[str] = None,
    slack_thread_ts: Optional[str] = None,
) -> Run:
    """Tick the archlinux branch until it converges.

    The fake client serves ``srcinfo_body`` for every download and stops the
    tree at the first dispatch, so a release shows up as exactly one build
    dispatch and a skip as none.
    """
    config = Config.from_yaml(CONFIG_PATH)
    state = ReleaseState.from_config(config)
    default_state = ReleaseState.from_config(config)
    state.meta.tag = tag
    state.meta.ephemeral.slack_channel_id = slack_channel_id
    state.meta.ephemeral.slack_thread_ts = slack_thread_ts
    if forced_release_type is not None:
        state.packages[PACKAGE].meta.release_type = forced_release_type

    dispatches: List[Dict[str, Any]] = []

    async def trigger_workflow(
        repo: str, workflow_file: str, inputs: Dict[str, str], ref: str
    ) -> None:
        dispatches.append(
            {
                "repo": repo,
                "workflow": workflow_file,
                "ref": ref,
                "inputs": dict(inputs),
            }
        )
        raise RuntimeError("stop after first dispatch")

    github_client = MagicMock(spec=GitHubClientAsync)
    github_client.download_file = AsyncMock(return_value=srcinfo_body)
    github_client.trigger_workflow = AsyncMock(side_effect=trigger_workflow)

    branch = get_factory(PackageType.ARCHLINUX).create_package_release_goal_tree_branch(
        state.packages,
        state.meta,
        default_state.packages[PACKAGE],
        github_client,
        PACKAGE,
    )
    status = await async_tick_tock(BehaviourTree(branch), cutoff=50)
    return Run(state, dispatches, github_client.download_file, status)


def test_archlinux_config_entry() -> None:
    state = ReleaseState.from_config(Config.from_yaml(CONFIG_PATH))
    meta = state.packages[PACKAGE].meta
    assert isinstance(meta, ArchlinuxMeta)
    assert meta.package_type == PackageType.ARCHLINUX
    assert meta.repo == "redis/redis-archlinux"
    assert meta.ref == "master"
    assert meta.publish_internal_release is True
    assert state.packages[PACKAGE].publish is not None


def test_archlinux_state_round_trip() -> None:
    state = ReleaseState.from_config(Config.from_yaml(CONFIG_PATH))
    state.packages[PACKAGE].meta.release_type = ReleaseType.INTERNAL

    restored = ReleaseState.model_validate_json(state.model_dump_json())

    meta = restored.packages[PACKAGE].meta
    assert type(meta) is ArchlinuxMeta
    assert meta.serialization_hint == "archlinux"
    assert meta.release_type == ReleaseType.INTERNAL
    assert restored == state


@pytest.mark.parametrize(
    "tag,remote,forced,expected_release_type",
    [
        ("8.4.1", "8.4.0", None, "public"),
        ("8.4.0", "8.4.0", None, "public"),
        ("8.12.0", "8.12rc2", None, "public"),
        ("8.6-rc1", "8.4.0", None, "internal"),
        ("8.2-rc1", "8.4.0", None, "internal"),
        ("8.4-m01", "8.4.0", None, "internal"),
        ("8.12-m01-int", "8.12.0", None, "internal"),
        ("8.12-rc1-int", "8.12.0", None, "internal"),
        ("8.4.1-int2", "8.4.1", None, "internal"),
        ("8.2.3", "8.4.0", ReleaseType.INTERNAL, "internal"),
    ],
)
async def test_archlinux_releases(
    tag: str,
    remote: str,
    forced: Optional[ReleaseType],
    expected_release_type: str,
) -> None:
    state, dispatches, _, _ = await run_release(tag, srcinfo(remote), forced)

    assert [(d["repo"], d["workflow"], d["ref"]) for d in dispatches] == [
        ("redis/redis-archlinux", "release_build_and_test.yml", "master")
    ]
    assert dispatches[0]["inputs"]["release_tag"] == tag
    assert dispatches[0]["inputs"]["release_type"] == expected_release_type
    assert state.packages[PACKAGE].meta.ephemeral.skip_message is None


@pytest.mark.parametrize(
    "tag,forced,downloads",
    [
        ("8.4.1", None, 1),
        ("8.6-rc1", None, 0),
        ("8.12-m01-int", None, 0),
        ("8.2.3", ReleaseType.INTERNAL, 0),
    ],
)
async def test_archlinux_reads_srcinfo_only_for_public(
    tag: str, forced: Optional[ReleaseType], downloads: int
) -> None:
    _, _, download_file, _ = await run_release(tag, srcinfo("8.4.0"), forced)

    assert download_file.await_count == downloads
    if downloads:
        download_file.assert_awaited_with("redis/redis-archlinux", ".SRCINFO", "master")


@pytest.mark.parametrize(
    "srcinfo_body",
    [
        "pkgbase = redis\n\tpkgdesc = x\n\tpkgrel = 1\n",
        srcinfo("8.12.r1.gabc"),
    ],
)
async def test_archlinux_fails_on_unreadable_pkgver(srcinfo_body: str) -> None:
    _, dispatches, _, status = await run_release("8.4.1", srcinfo_body)

    assert dispatches == []
    assert status == Status.FAILURE


@pytest.mark.parametrize("tag", ["8.4.1", "8.6-rc1"])
async def test_archlinux_passes_slack_inputs(tag: str) -> None:
    _, dispatches, _, _ = await run_release(
        tag, srcinfo("8.4.0"), slack_channel_id="C123", slack_thread_ts="1700.01"
    )

    assert dispatches[0]["inputs"]["slack_channel_id"] == "C123"
    assert dispatches[0]["inputs"]["slack_thread_ts"] == "1700.01"


async def test_archlinux_omits_unset_slack_inputs() -> None:
    _, dispatches, _, _ = await run_release("8.4.1", srcinfo("8.4.0"))

    assert "slack_channel_id" not in dispatches[0]["inputs"]
    assert "slack_thread_ts" not in dispatches[0]["inputs"]


@pytest.mark.parametrize(
    "tag,remote,forced",
    [
        ("8.2.3", "8.4.0", None),
        ("8.2.3", "8.4.0", ReleaseType.PUBLIC),
        ("8.10.0", "8.12rc2", None),
    ],
)
async def test_archlinux_skips_public_downgrade(
    tag: str, remote: str, forced: Optional[ReleaseType]
) -> None:
    state, dispatches, _, _ = await run_release(tag, srcinfo(remote), forced)

    assert dispatches == []
    assert state.packages[PACKAGE].meta.ephemeral.skip_message is not None


@pytest.mark.parametrize("tag", ["unstable", "custom-build-xyz"])
async def test_archlinux_skips_non_release_tags(tag: str) -> None:
    state, dispatches, download_file, _ = await run_release(tag, srcinfo("8.4.0"))

    meta = state.packages[PACKAGE].meta
    assert isinstance(meta, ArchlinuxMeta)
    assert dispatches == []
    assert download_file.await_count == 0
    assert meta.ephemeral.is_version_acceptable is False
