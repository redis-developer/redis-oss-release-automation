#!/usr/bin/env python3
"""Integration test for the simplified Parallel composite with the tree structure."""

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Sequence

from redis_release.bht.composites import ParallelBarrier


class SimpleAction(Behaviour):
    """Simple action that succeeds after N ticks."""

    def __init__(self, name: str, ticks_to_complete: int = 1):
        super().__init__(name)
        self.ticks_to_complete = ticks_to_complete
        self.tick_count = 0

    def update(self) -> Status:
        self.tick_count += 1
        if self.tick_count >= self.ticks_to_complete:
            return Status.SUCCESS
        return Status.RUNNING


class SimpleCondition(Behaviour):
    """Simple condition that always returns SUCCESS."""

    def __init__(self, name: str):
        super().__init__(name)

    def update(self) -> Status:
        return Status.SUCCESS


def test_parallel_with_sequences():
    """Test parallel with sequence children (similar to package release structure)."""
    print("\n=== Test: Parallel with Sequence children ===")

    # Create sequences that simulate package releases
    package1 = Sequence(
        "Package 1",
        memory=True,
        children=[
            SimpleCondition("Check Package 1"),
            SimpleAction("Build Package 1", ticks_to_complete=2),
            SimpleAction("Publish Package 1", ticks_to_complete=1),
        ],
    )

    package2 = Sequence(
        "Package 2",
        memory=True,
        children=[
            SimpleCondition("Check Package 2"),
            SimpleAction("Build Package 2", ticks_to_complete=1),
            SimpleAction("Publish Package 2", ticks_to_complete=2),
        ],
    )

    package3 = Sequence(
        "Package 3",
        memory=True,
        children=[
            SimpleCondition("Check Package 3"),
            SimpleAction("Build Package 3", ticks_to_complete=1),
            SimpleAction("Publish Package 3", ticks_to_complete=1),
        ],
    )

    # Create parallel to run all packages
    parallel = ParallelBarrier(
        "Release All Packages",
        children=[package1, package2, package3],
    )

    # Tick until completion
    tick_count = 0
    while parallel.status == Status.RUNNING or tick_count == 0:
        tick_count += 1
        print(f"\n--- Tick {tick_count} ---")
        list(parallel.tick())
        print(f"Parallel status: {parallel.status}")
        print(f"  Package 1: {package1.status}")
        print(f"  Package 2: {package2.status}")
        print(f"  Package 3: {package3.status}")

        if tick_count > 10:
            print("ERROR: Too many ticks!")
            break

    print(f"\nFinal status: {parallel.status}")
    assert parallel.status == Status.SUCCESS, f"Expected SUCCESS, got {parallel.status}"
    assert package1.status == Status.SUCCESS
    assert package2.status == Status.SUCCESS
    assert package3.status == Status.SUCCESS
    print("✓ Test passed!")


def test_parallel_with_one_failing_sequence():
    """Test parallel where one sequence fails."""
    print("\n=== Test: Parallel with one failing sequence ===")

    class FailingAction(Behaviour):
        def __init__(self, name: str):
            super().__init__(name)

        def update(self) -> Status:
            return Status.FAILURE

    package1 = Sequence(
        "Package 1",
        memory=True,
        children=[
            SimpleAction("Build Package 1", ticks_to_complete=1),
        ],
    )

    package2 = Sequence(
        "Package 2 (will fail)",
        memory=True,
        children=[
            SimpleAction("Build Package 2", ticks_to_complete=1),
            FailingAction("Publish Package 2 (fails)"),
        ],
    )

    package3 = Sequence(
        "Package 3",
        memory=True,
        children=[
            SimpleAction("Build Package 3", ticks_to_complete=1),
        ],
    )

    parallel = ParallelBarrier(
        "Release All Packages",
        children=[package1, package2, package3],
    )

    # Tick until completion
    tick_count = 0
    while parallel.status == Status.RUNNING or tick_count == 0:
        tick_count += 1
        print(f"\n--- Tick {tick_count} ---")
        list(parallel.tick())
        print(f"Parallel status: {parallel.status}")
        print(f"  Package 1: {package1.status}")
        print(f"  Package 2: {package2.status}")
        print(f"  Package 3: {package3.status}")

        if tick_count > 10:
            print("ERROR: Too many ticks!")
            break

    print(f"\nFinal status: {parallel.status}")
    assert parallel.status == Status.FAILURE, f"Expected FAILURE, got {parallel.status}"
    print("✓ Test passed!")


def test_synchronized_behavior_with_sequences():
    """Test that completed sequences are not re-ticked."""
    print("\n=== Test: Synchronized behavior with sequences ===")

    # Track how many times each action is ticked
    action1 = SimpleAction("Action 1", ticks_to_complete=1)
    action2 = SimpleAction("Action 2", ticks_to_complete=3)
    action3 = SimpleAction("Action 3", ticks_to_complete=1)

    seq1 = Sequence("Seq 1", memory=True, children=[action1])
    seq2 = Sequence("Seq 2", memory=True, children=[action2])
    seq3 = Sequence("Seq 3", memory=True, children=[action3])

    parallel = ParallelBarrier("Parallel", children=[seq1, seq2, seq3])

    # Tick until completion
    tick_count = 0
    while parallel.status == Status.RUNNING or tick_count == 0:
        tick_count += 1
        list(parallel.tick())

    print(f"Total ticks: {tick_count}")
    print(f"Action 1 tick count: {action1.tick_count}")
    print(f"Action 2 tick count: {action2.tick_count}")
    print(f"Action 3 tick count: {action3.tick_count}")

    # Verify synchronized behavior
    assert (
        action1.tick_count == 1
    ), f"Action 1 should be ticked once, got {action1.tick_count}"
    assert (
        action2.tick_count == 3
    ), f"Action 2 should be ticked 3 times, got {action2.tick_count}"
    assert (
        action3.tick_count == 1
    ), f"Action 3 should be ticked once, got {action3.tick_count}"
    assert parallel.status == Status.SUCCESS
    print("✓ Test passed!")


if __name__ == "__main__":
    test_parallel_with_sequences()
    test_parallel_with_one_failing_sequence()
    test_synchronized_behavior_with_sequences()
    print("\n✅ All integration tests passed!")
