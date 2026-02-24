"""Tests for ConditionGuard decorator."""

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.trees import BehaviourTree

from redis_release.bht.decorators import ConditionGuard


class ConditionStateBehaviour(Behaviour):
    """A behaviour that sets a condition to True and returns a configurable status."""

    def __init__(
        self, name: str, condition_state: dict, return_status: Status = Status.RUNNING
    ) -> None:
        super().__init__(name=name)
        self.condition_state = condition_state
        self.return_status = return_status
        self.update_call_count = 0

    def update(self) -> Status:
        self.update_call_count += 1
        # Make condition become true
        self.condition_state["value"] = True
        return self.return_status


class TrackingBehaviour(Behaviour):
    """A behaviour that tracks whether it was called and returns a configurable status."""

    def __init__(self, name: str, return_status: Status = Status.RUNNING) -> None:
        super().__init__(name=name)
        self.return_status = return_status
        self.update_call_count = 0

    def update(self) -> Status:
        self.update_call_count += 1
        return self.return_status


class TestConditionGuardWithDecoratedBehaviour:
    """Test ConditionGuard with decorated behaviour that modifies condition."""

    def test_first_tick_returns_running_when_child_returns_running(self) -> None:
        """Test that first tick returns RUNNING when child returns RUNNING.

        Setup:
        - ConditionGuard wraps a decorated behaviour
        - Condition is False initially
        - Decorated behaviour makes condition become true and returns RUNNING
        - After first tick, tree should be RUNNING (child's status)
        """
        condition_state = {"value": False}

        def condition() -> bool:
            return condition_state["value"]

        child = ConditionStateBehaviour(
            name="child", condition_state=condition_state, return_status=Status.RUNNING
        )
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=condition,
            guard_status=Status.SUCCESS,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # Before first tick, condition is False
        assert condition_state["value"] is False

        # First tick: condition is False, child executes and returns RUNNING
        tree.tick()

        # After first tick: condition is True, guard returns child's RUNNING status
        assert condition_state["value"] is True
        assert guard.status == Status.RUNNING
        assert child.update_call_count == 1

    def test_second_tick_returns_guard_status_and_child_not_called(self) -> None:
        """Test that second tick returns guard_status and child is not called.

        After first tick sets condition to True:
        - Second tick should check condition (True) and return guard_status
        - Child should NOT be called on second tick
        """
        condition_state = {"value": False}

        def condition() -> bool:
            return condition_state["value"]

        child = ConditionStateBehaviour(
            name="child", condition_state=condition_state, return_status=Status.RUNNING
        )
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=condition,
            guard_status=Status.SUCCESS,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # First tick: child executes, sets condition to True
        tree.tick()
        assert child.update_call_count == 1
        assert guard.status == Status.RUNNING

        # Second tick: condition is True, guard returns SUCCESS without calling child
        tree.tick()
        assert child.update_call_count == 1  # Child NOT called again
        assert guard.status == Status.SUCCESS  # Guard status returned


class TestConditionGuardStatusCombinations:
    """Test ConditionGuard with different guard_status and child status combinations."""

    def test_guard_failure_child_success(self) -> None:
        """Test guard_status=FAILURE with child returning SUCCESS.

        First tick: condition False, child executes and returns SUCCESS
        Second tick: condition True, guard returns FAILURE without calling child
        """
        condition_state = {"value": False}

        def condition() -> bool:
            return condition_state["value"]

        child = ConditionStateBehaviour(
            name="child", condition_state=condition_state, return_status=Status.SUCCESS
        )
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=condition,
            guard_status=Status.FAILURE,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # First tick: child executes and returns SUCCESS
        tree.tick()
        assert child.update_call_count == 1
        assert guard.status == Status.SUCCESS
        assert condition_state["value"] is True

        # Second tick: condition True, guard returns FAILURE
        tree.tick()
        assert child.update_call_count == 1  # Child NOT called
        assert guard.status == Status.FAILURE

    def test_guard_success_child_failure(self) -> None:
        """Test guard_status=SUCCESS with child returning FAILURE.

        First tick: condition False, child executes and returns FAILURE
        Second tick: condition True, guard returns SUCCESS without calling child
        """
        condition_state = {"value": False}

        def condition() -> bool:
            return condition_state["value"]

        child = ConditionStateBehaviour(
            name="child", condition_state=condition_state, return_status=Status.FAILURE
        )
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=condition,
            guard_status=Status.SUCCESS,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # First tick: child executes and returns FAILURE
        tree.tick()
        assert child.update_call_count == 1
        assert guard.status == Status.FAILURE
        assert condition_state["value"] is True

        # Second tick: condition True, guard returns SUCCESS
        tree.tick()
        assert child.update_call_count == 1  # Child NOT called
        assert guard.status == Status.SUCCESS

    def test_guard_failure_child_running(self) -> None:
        """Test guard_status=FAILURE with child returning RUNNING.

        First tick: condition False, child executes and returns RUNNING
        Second tick: condition True, guard returns FAILURE without calling child
        """
        condition_state = {"value": False}

        def condition() -> bool:
            return condition_state["value"]

        child = ConditionStateBehaviour(
            name="child", condition_state=condition_state, return_status=Status.RUNNING
        )
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=condition,
            guard_status=Status.FAILURE,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # First tick: child executes and returns RUNNING
        tree.tick()
        assert child.update_call_count == 1
        assert guard.status == Status.RUNNING

        # Second tick: condition True, guard returns FAILURE
        tree.tick()
        assert child.update_call_count == 1  # Child NOT called
        assert guard.status == Status.FAILURE


class TestConditionGuardNeverCallsChildWhenConditionMet:
    """Test that decorated behaviour is never called if condition is already met."""

    def test_child_never_called_when_condition_initially_true(self) -> None:
        """Test that child is never called when condition is True from the start."""
        child = TrackingBehaviour(name="child", return_status=Status.SUCCESS)
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=lambda: True,  # Always True
            guard_status=Status.FAILURE,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # Multiple ticks - child should never be called
        for _ in range(5):
            tree.tick()
            assert child.update_call_count == 0  # Child NEVER called
            assert guard.status == Status.FAILURE

    def test_child_never_called_when_condition_true_with_success_guard(self) -> None:
        """Test with guard_status=SUCCESS - child is never called."""
        child = TrackingBehaviour(name="child", return_status=Status.FAILURE)
        guard = ConditionGuard(
            name="test_guard",
            child=child,
            condition=lambda: True,  # Always True
            guard_status=Status.SUCCESS,
        )
        tree = BehaviourTree(root=guard)
        tree.setup()

        # Multiple ticks - child should never be called
        for _ in range(3):
            tree.tick()
            assert child.update_call_count == 0  # Child NEVER called
            assert guard.status == Status.SUCCESS
