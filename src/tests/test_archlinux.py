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
from redis_release.models import PackageType, RedisModule, ReleaseType

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
    custom_build: bool = False,
    nightly_build: bool = False,
    module_versions: Optional[Dict[RedisModule, str]] = None,
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
    state.meta.is_custom_build = custom_build
    state.meta.is_nightly_build = nightly_build
    meta = state.packages[PACKAGE].meta
    assert isinstance(meta, ArchlinuxMeta)
    meta.module_versions = module_versions
    if forced_release_type is not None:
        meta.release_type = forced_release_type

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

    assert [(d["repo"], d["workflow"]) for d in dispatches] == [
        ("redis/redis-archlinux", "release_build_and_test.yml")
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
        release_ref = Config.from_yaml(CONFIG_PATH).packages[PACKAGE].ref
        download_file.assert_awaited_with(
            "redis/redis-archlinux", ".SRCINFO", release_ref
        )


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


@pytest.mark.parametrize(
    "tag,run_type", [("unstable", "unstable"), ("custom-build-xyz", "custom")]
)
async def test_archlinux_builds_non_version_tags_from_unstable_branch(
    tag: str, run_type: str
) -> None:
    state, dispatches, download_file, _ = await run_release(tag, srcinfo("8.4.0"))

    assert [(d["workflow"], d["ref"]) for d in dispatches] == [
        ("release_build_and_test.yml", "unstable")
    ]
    assert dispatches[0]["inputs"]["run_type"] == run_type
    assert dispatches[0]["inputs"]["release_type"] == "internal"
    assert download_file.await_count == 0


async def test_archlinux_nightly_sends_modules_at_master() -> None:
    modules = {module: "master" for module in RedisModule}
    _, dispatches, _, _ = await run_release(
        "unstable",
        srcinfo("8.4.0"),
        custom_build=True,
        nightly_build=True,
        module_versions=modules,
    )

    inputs = dispatches[0]["inputs"]
    assert dispatches[0]["ref"] == "unstable"
    assert inputs["run_type"] == "nightly"
    assert inputs["release_type"] == "internal"
    for module in RedisModule:
        assert inputs[f"{module.value}_version"] == "master"


async def test_archlinux_custom_build_of_version_tag_stays_on_release_branch() -> None:
    state, dispatches, download_file, _ = await run_release(
        "8.4.1",
        srcinfo("8.4.0"),
        custom_build=True,
        module_versions={RedisModule.SEARCH: "master"},
    )

    release_ref = Config.from_yaml(CONFIG_PATH).packages[PACKAGE].ref
    inputs = dispatches[0]["inputs"]
    assert dispatches[0]["ref"] == release_ref
    assert inputs["run_type"] == "custom"
    assert inputs["release_type"] == "internal"
    assert inputs["redisearch_version"] == "master"
    assert download_file.await_count == 0


async def test_archlinux_release_sends_run_type_release() -> None:
    _, dispatches, _, _ = await run_release("8.6-rc1", srcinfo("8.4.0"))

    assert dispatches[0]["inputs"]["run_type"] == "release"


@pytest.mark.parametrize(
    "tag,nightly,expected",
    [
        ("8.6-rc1", False, Status.SUCCESS),
        ("unstable", False, Status.FAILURE),
        ("unstable", True, Status.FAILURE),
        ("8.6-rc1", True, Status.FAILURE),
    ],
)
def test_archlinux_publishes_only_release_runs(
    tag: str, nightly: bool, expected: Status
) -> None:
    state = ReleaseState.from_config(Config.from_yaml(CONFIG_PATH))
    state.meta.tag = tag
    state.meta.is_custom_build = nightly
    state.meta.is_nightly_build = nightly
    meta = state.packages[PACKAGE].meta
    meta.release_type = ReleaseType.INTERNAL

    need_to_publish = get_factory(
        PackageType.ARCHLINUX
    ).create_need_to_publish_behaviour("Need To Publish?", meta, state.meta, PACKAGE)

    assert need_to_publish.update() == expected


def test_archlinux_publish_inputs_match_publish_workflow() -> None:
    """release_publish.yml rejects any input it does not declare."""
    state = ReleaseState.from_config(Config.from_yaml(CONFIG_PATH))
    state.meta.tag = "8.12-rc3"
    package = state.packages[PACKAGE]
    assert isinstance(package.meta, ArchlinuxMeta)
    package.meta.release_type = ReleaseType.INTERNAL
    package.meta.module_versions = {RedisModule.SEARCH: "master"}

    behaviour = get_factory(PackageType.ARCHLINUX).create_publish_workflow_inputs(
        "Set Publish Workflow Inputs", package.publish, package.meta, state.meta, ""
    )
    behaviour.tick_once()

    assert package.publish.inputs == {
        "release_type": "internal",
        "release_tag": "8.12-rc3",
    }
