#!/usr/bin/env python3
"""Test the simplified Parallel composite."""

from py_trees.behaviour import Behaviour
from py_trees.common import Status

from redis_release.bht.composites import ParallelBarrier


class MockBehaviour(Behaviour):
    """Mock behaviour for testing."""

    def __init__(self, name: str, return_status: Status, ticks_until_done: int = 1):
        super().__init__(name)
        self.return_status = return_status
        self.ticks_until_done = ticks_until_done
        self.tick_count = 0

    def update(self) -> Status:
        self.tick_count += 1
        if self.tick_count >= self.ticks_until_done:
            return self.return_status
        return Status.RUNNING


def test_all_success():
    """Test that parallel returns SUCCESS when all children succeed."""
    print("\n=== Test: All children succeed ===")
    parallel = ParallelBarrier(
        "Test Parallel",
        children=[
            MockBehaviour("Child 1", Status.SUCCESS, ticks_until_done=1),
            MockBehaviour("Child 2", Status.SUCCESS, ticks_until_done=2),
            MockBehaviour("Child 3", Status.SUCCESS, ticks_until_done=1),
        ],
    )

    # First tick - some children still running
    list(parallel.tick())
    print(f"After tick 1: {parallel.status}")
    print(f"  Child 1: {parallel.children[0].status}")
    print(f"  Child 2: {parallel.children[1].status}")
    print(f"  Child 3: {parallel.children[2].status}")

    # Second tick - all should succeed
    list(parallel.tick())
    print(f"After tick 2: {parallel.status}")
    print(f"  Child 1: {parallel.children[0].status}")
    print(f"  Child 2: {parallel.children[1].status}")
    print(f"  Child 3: {parallel.children[2].status}")

    assert parallel.status == Status.SUCCESS, f"Expected SUCCESS, got {parallel.status}"
    print("✓ Test passed!")


def test_one_failure():
    """Test that parallel returns FAILURE when one child fails."""
    print("\n=== Test: One child fails ===")
    parallel = ParallelBarrier(
        "Test Parallel",
        children=[
            MockBehaviour("Child 1", Status.SUCCESS, ticks_until_done=1),
            MockBehaviour("Child 2", Status.FAILURE, ticks_until_done=2),
            MockBehaviour("Child 3", Status.SUCCESS, ticks_until_done=1),
        ],
    )

    # First tick
    list(parallel.tick())
    print(f"After tick 1: {parallel.status}")
    print(f"  Child 1: {parallel.children[0].status}")
    print(f"  Child 2: {parallel.children[1].status}")
    print(f"  Child 3: {parallel.children[2].status}")

    # Second tick - child 2 fails
    list(parallel.tick())
    print(f"After tick 2: {parallel.status}")
    print(f"  Child 1: {parallel.children[0].status}")
    print(f"  Child 2: {parallel.children[1].status}")
    print(f"  Child 3: {parallel.children[2].status}")

    assert parallel.status == Status.FAILURE, f"Expected FAILURE, got {parallel.status}"
    print("✓ Test passed!")


def test_synchronized_mode():
    """Test that converged children are skipped on subsequent ticks."""
    print("\n=== Test: Synchronized mode (skip converged children) ===")

    # Create children that track how many times they're ticked
    child1 = MockBehaviour("Child 1", Status.SUCCESS, ticks_until_done=1)
    child2 = MockBehaviour("Child 2", Status.SUCCESS, ticks_until_done=3)
    child3 = MockBehaviour("Child 3", Status.SUCCESS, ticks_until_done=1)

    parallel = ParallelBarrier("Test Parallel", children=[child1, child2, child3])

    # First tick - child1 and child3 succeed, child2 still running
    list(parallel.tick())
    print(f"After tick 1:")
    print(f"  Child 1: {child1.status}, tick_count={child1.tick_count}")
    print(f"  Child 2: {child2.status}, tick_count={child2.tick_count}")
    print(f"  Child 3: {child3.status}, tick_count={child3.tick_count}")

    # Second tick - only child2 should be ticked
    list(parallel.tick())
    print(f"After tick 2:")
    print(f"  Child 1: {child1.status}, tick_count={child1.tick_count}")
    print(f"  Child 2: {child2.status}, tick_count={child2.tick_count}")
    print(f"  Child 3: {child3.status}, tick_count={child3.tick_count}")

    # Third tick - only child2 should be ticked again
    list(parallel.tick())
    print(f"After tick 3:")
    print(f"  Child 1: {child1.status}, tick_count={child1.tick_count}")
    print(f"  Child 2: {child2.status}, tick_count={child2.tick_count}")
    print(f"  Child 3: {child3.status}, tick_count={child3.tick_count}")

    # Verify that child1 and child3 were only ticked once (synchronized mode)
    assert (
        child1.tick_count == 1
    ), f"Child 1 should be ticked once, got {child1.tick_count}"
    assert (
        child3.tick_count == 1
    ), f"Child 3 should be ticked once, got {child3.tick_count}"
    assert (
        child2.tick_count == 3
    ), f"Child 2 should be ticked 3 times, got {child2.tick_count}"
    assert parallel.status == Status.SUCCESS, f"Expected SUCCESS, got {parallel.status}"
    print("✓ Test passed!")


def test_empty_children():
    """Test that parallel with no children returns SUCCESS."""
    print("\n=== Test: Empty children ===")
    parallel = ParallelBarrier("Test Parallel", children=[])

    list(parallel.tick())
    print(f"Status: {parallel.status}")

    assert parallel.status == Status.SUCCESS, f"Expected SUCCESS, got {parallel.status}"
    print("✓ Test passed!")


if __name__ == "__main__":
    test_all_success()
    test_one_failure()
    test_synchronized_mode()
    test_empty_children()
    print("\n✅ All tests passed!")
