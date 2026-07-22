"""Tests for the homebrew-formula version gate (NeedToReleaseFormula)."""

from typing import Optional

import pytest
from py_trees.common import Status

from redis_release.bht.behaviours_formula import NeedToReleaseFormula
from redis_release.bht.state import PackageMeta, ReleaseMeta


def _gate(tag: Optional[str]) -> NeedToReleaseFormula:
    return NeedToReleaseFormula("Need To Release?", PackageMeta(), ReleaseMeta(tag=tag))


# tag -> should the formula test run? (unstable or version >= 8.10)
@pytest.mark.parametrize(
    "tag,expected",
    [
        ("unstable", True),
        ("8.10-rc2", True),
        ("8.10-m01", True),
        ("8.10.0", True),
        ("8.10.0-int1", True),
        ("8.11.3", True),
        ("9.0.0", True),
        ("8.9.5", False),
        ("8.2.1", False),
        ("7.4.0", False),
    ],
)
def test_gate_by_version(tag: str, expected: bool) -> None:
    behaviour = _gate(tag)
    status = behaviour.update()
    if expected:
        assert status == Status.SUCCESS
        assert behaviour.package_meta.ephemeral.skip_message is None
    else:
        assert status == Status.FAILURE
        assert behaviour.package_meta.ephemeral.skip_message is not None


def test_gate_skips_non_version_tag() -> None:
    behaviour = _gate("custom-build-xyz")
    assert behaviour.update() == Status.FAILURE
    assert behaviour.package_meta.ephemeral.skip_message is not None


@pytest.mark.parametrize("tag", [None, ""])
def test_gate_skips_missing_tag(tag: Optional[str]) -> None:
    behaviour = _gate(tag)
    assert behaviour.update() == Status.FAILURE
    assert behaviour.package_meta.ephemeral.skip_message is not None


def test_gate_clears_stale_skip_message_when_running() -> None:
    behaviour = _gate("8.10.0")
    behaviour.package_meta.ephemeral.skip_message = "stale from a previous run"
    assert behaviour.update() == Status.SUCCESS
    assert behaviour.package_meta.ephemeral.skip_message is None
