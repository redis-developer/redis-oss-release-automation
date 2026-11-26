"""Tests for the Fedora package type (NeedToReleaseFedora, DetectReleaseTypeFedora)."""

from typing import Optional

import pytest
from py_trees.common import Status

from redis_release.bht.behaviours_fedora import (
    DetectReleaseTypeFedora,
    NeedToReleaseFedora,
)
from redis_release.bht.state import PackageMeta, ReleaseMeta
from redis_release.bht.tree_factory import get_factory
from redis_release.bht.tree_factory_fedora import FedoraFactory
from redis_release.models import PackageType, ReleaseType


def _gate(tag: Optional[str]) -> NeedToReleaseFedora:
    return NeedToReleaseFedora("Need To Release?", PackageMeta(), ReleaseMeta(tag=tag))


def _detect(tag: Optional[str]) -> DetectReleaseTypeFedora:
    return DetectReleaseTypeFedora(
        "Detect Release Type", PackageMeta(), ReleaseMeta(tag=tag)
    )


# tag -> does the Fedora release run? (version >= 8.12)
@pytest.mark.parametrize(
    "tag,expected",
    [
        ("8.12-rc1", True),
        ("8.12-m01", True),
        ("8.12.0", True),
        ("8.12.0-int1", True),
        ("8.12.3", True),
        ("8.14.0", True),
        ("9.0.0", True),
        ("8.10.4", False),
        ("8.2.1", False),
        ("7.4.0", False),
    ],
)
def test_gate_by_version(tag: str, expected: bool) -> None:
    behaviour = _gate(tag)
    behaviour.initialise()
    status = behaviour.update()
    if expected:
        assert status == Status.SUCCESS
        assert behaviour.package_meta.ephemeral.skip_message is None
    else:
        assert status == Status.FAILURE
        assert behaviour.package_meta.ephemeral.skip_message is not None
        assert "8.12" in behaviour.package_meta.ephemeral.skip_message


@pytest.mark.parametrize("tag", ["unstable", "custom-build-xyz", None])
def test_gate_does_not_release_other_tags(tag: Optional[str]) -> None:
    behaviour = _gate(tag)
    behaviour.initialise()
    assert behaviour.update() == Status.FAILURE


def test_gate_clears_stale_skip_message_when_running() -> None:
    behaviour = _gate("8.12.0")
    behaviour.package_meta.ephemeral.skip_message = "stale from a previous run"
    behaviour.initialise()
    assert behaviour.update() == Status.SUCCESS
    assert behaviour.package_meta.ephemeral.skip_message is None


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("8.12.0", ReleaseType.PUBLIC),
        ("8.12.3", ReleaseType.PUBLIC),
        ("8.14-rc1", ReleaseType.INTERNAL),
        ("8.14-m01", ReleaseType.INTERNAL),
        ("8.12.0-int1", ReleaseType.INTERNAL),
    ],
)
def test_detect_release_type(tag: str, expected: ReleaseType) -> None:
    behaviour = _detect(tag)
    behaviour.initialise()
    assert behaviour.update() == Status.SUCCESS
    assert behaviour.package_meta.release_type == expected


def test_detect_keeps_release_type_from_state() -> None:
    behaviour = _detect("8.12.0")
    behaviour.package_meta.release_type = ReleaseType.INTERNAL
    behaviour.initialise()
    assert behaviour.update() == Status.SUCCESS
    assert behaviour.package_meta.release_type == ReleaseType.INTERNAL


def test_factory_is_registered() -> None:
    assert isinstance(get_factory(PackageType.FEDORA), FedoraFactory)
