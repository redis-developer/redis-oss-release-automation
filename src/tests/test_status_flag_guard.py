"""Tests for StatusFlagGuard decorator."""

from typing import Optional

import pytest
from py_trees import common
from py_trees.behaviour import Behaviour
from py_trees.common import Status
from pydantic import BaseModel

from redis_release.bht.decorators import StatusFlagGuard


class StatusFlagContainer(BaseModel):
    """Container for holding status flags."""

    status_flag: Optional[Status] = None


class SuccessBehaviour(Behaviour):
    """A behaviour that always succeeds."""

    def update(self) -> Status:
        return Status.SUCCESS


class FailureBehaviour(Behaviour):
    """A behaviour that always fails."""

    def update(self) -> Status:
        return Status.FAILURE


class RunningBehaviour(Behaviour):
    """A behaviour that always returns RUNNING."""

    def update(self) -> Status:
        return Status.RUNNING


class TestStatusFlagGuardInitialization:
    """Test StatusFlagGuard initialization."""

    def test_init_with_valid_container(self) -> None:
        """Test initialization with valid container."""
        container = StatusFlagContainer()
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
        )
        assert guard.name == "test_guard"
        assert guard.flag == "status_flag"
        assert guard.guard_status == Status.FAILURE

    def test_init_with_none_name_failure(self) -> None:
        """Test initialization with None name generates default name for FAILURE."""
        container = StatusFlagContainer()
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name=None,
            child=child,
            container=container,
            flag="status_flag",
            guard_status=common.Status.FAILURE,
        )
        assert guard.name == "Unless status_flag failed"

    def test_init_with_none_name_success(self) -> None:
        """Test initialization with None name generates default name for SUCCESS."""
        container = StatusFlagContainer()
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name=None,
            child=child,
            container=container,
            flag="status_flag",
            guard_status=common.Status.SUCCESS,
        )
        assert guard.name == "Unless status_flag succeeded"

    def test_init_with_custom_guard_status(self) -> None:
        """Test initialization with custom guard_status."""
        container = StatusFlagContainer()
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.SUCCESS,
        )
        assert guard.guard_status == Status.SUCCESS

    def test_init_with_nonexistent_field(self) -> None:
        """Test initialization fails with nonexistent field."""
        container = StatusFlagContainer()
        child = SuccessBehaviour(name="child")
        with pytest.raises(ValueError, match="Field 'nonexistent' does not exist"):
            StatusFlagGuard(
                name="test_guard",
                child=child,
                container=container,
                flag="nonexistent",
            )

    def test_init_with_invalid_flag_type(self) -> None:
        """Test initialization fails when flag has invalid type."""

        class BadContainer(BaseModel):
            status_flag: str = "invalid"

        container = BadContainer()
        child = SuccessBehaviour(name="child")
        with pytest.raises(TypeError, match="must be either common.Status or None"):
            StatusFlagGuard(
                name="test_guard",
                child=child,
                container=container,
                flag="status_flag",
            )

    def test_init_with_invalid_guard_status(self) -> None:
        """Test initialization fails with invalid guard_status."""
        container = StatusFlagContainer()
        child = SuccessBehaviour(name="child")
        with pytest.raises(
            ValueError,
            match="guard_status must be FAILURE, SUCCESS, or None, got Status.RUNNING",
        ):
            StatusFlagGuard(
                name="test_guard",
                child=child,
                container=container,
                flag="status_flag",
                guard_status=Status.RUNNING,
            )


class TestStatusFlagGuardGuarding:
    """Test StatusFlagGuard guarding behavior."""

    def test_guard_prevents_execution_when_flag_matches_guard_status(self) -> None:
        """Test that guard prevents execution when flag matches guard_status."""
        container = StatusFlagContainer(status_flag=Status.FAILURE)
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.FAILURE,
        )

        # Guard should return FAILURE without executing child
        status = guard.update()
        assert status == Status.FAILURE

    def test_guard_allows_execution_when_flag_is_none(self) -> None:
        """Test that guard allows execution when flag is None."""
        container = StatusFlagContainer(status_flag=None)
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.FAILURE,
        )

        # Child should execute and return SUCCESS
        guard.decorated.status = Status.SUCCESS
        status = guard.update()
        assert status == Status.SUCCESS

    def test_guard_allows_execution_when_flag_differs_from_guard_status(self) -> None:
        """Test that guard allows execution when flag differs from guard_status."""
        container = StatusFlagContainer(status_flag=Status.SUCCESS)
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.FAILURE,
        )

        # Child should execute and return SUCCESS
        guard.decorated.status = Status.SUCCESS
        status = guard.update()
        assert status == Status.SUCCESS


class TestStatusFlagGuardFlagUpdate:
    """Test StatusFlagGuard flag update behavior."""

    def test_flag_updated_on_child_success(self) -> None:
        """Test that flag is updated to child's status on success."""
        container = StatusFlagContainer(status_flag=None)
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
        )

        # Simulate child execution - update() should update the flag
        guard.decorated.status = Status.SUCCESS
        status = guard.update()
        assert status == Status.SUCCESS
        assert container.status_flag == Status.SUCCESS

    def test_flag_updated_on_child_failure(self) -> None:
        """Test that flag is updated to child's status on failure."""
        container = StatusFlagContainer(status_flag=None)
        child = FailureBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
        )

        # Simulate child execution - update() should update the flag
        guard.decorated.status = Status.FAILURE
        status = guard.update()
        assert status == Status.FAILURE
        assert container.status_flag == Status.FAILURE

    def test_flag_updated_on_child_running(self) -> None:
        """Test that flag is updated to child's status on RUNNING."""
        container = StatusFlagContainer(status_flag=None)
        child = RunningBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
        )

        # Simulate child execution - update() should update the flag even for RUNNING
        guard.decorated.status = Status.RUNNING
        status = guard.update()
        assert status == Status.RUNNING
        assert container.status_flag == Status.RUNNING

    def test_flag_not_updated_when_guard_active(self) -> None:
        """Test that flag is not updated when guard is active."""
        container = StatusFlagContainer(status_flag=Status.FAILURE)
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.FAILURE,
        )

        # Guard is active, flag should not be updated
        guard.decorated.status = Status.SUCCESS
        status = guard.update()
        assert status == Status.FAILURE
        assert container.status_flag == Status.FAILURE


class TestStatusFlagGuardWithDifferentGuardStatus:
    """Test StatusFlagGuard with different guard_status values."""

    def test_guard_with_failure_status(self) -> None:
        """Test guard with FAILURE as guard_status."""
        container = StatusFlagContainer(status_flag=Status.FAILURE)
        child = SuccessBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.FAILURE,
        )

        # Guard should return FAILURE
        status = guard.update()
        assert status == Status.FAILURE

    def test_guard_with_success_status(self) -> None:
        """Test guard with SUCCESS as guard_status."""
        container = StatusFlagContainer(status_flag=Status.SUCCESS)
        child = FailureBehaviour(name="child")
        guard = StatusFlagGuard(
            name="test_guard",
            child=child,
            container=container,
            flag="status_flag",
            guard_status=Status.SUCCESS,
        )

        # Guard should return SUCCESS
        status = guard.update()
        assert status == Status.SUCCESS
