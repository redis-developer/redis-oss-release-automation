from struct import pack

from rich.pretty import pretty_repr

from redis_release.bht.state import Package, PackageMeta, ReleaseState, Workflow
from redis_release.state_display import DisplayModelGeneric, Step, StepStatus


def create_test_release_state() -> ReleaseState:
    state = ReleaseState()

    package = Package(
        meta=PackageMeta(),
        build=Workflow(),
    )

    state.packages["test-package"] = package

    return state


class TestDisplayModel:
    def test_instantiate_display_model(self) -> None:
        state = create_test_release_state()
        model = DisplayModelGeneric()

        package = state.packages["test-package"]
        workflow = package.build
        package.meta.ref = "boo"
        status, steps = model.get_workflow_status(package, workflow)
        print(pretty_repr(steps))
        print(status)

    def test_empty_state_returns_not_started(self) -> None:
        """Test that an empty workflow state returns NOT_STARTED status."""
        state = create_test_release_state()
        model = DisplayModelGeneric()

        package = state.packages["test-package"]
        workflow = package.build

        status, steps = model.get_workflow_status(package, workflow)

        assert status == StepStatus.NOT_STARTED
        assert len(steps) > 0  # Should have at least one step

    def test_first_step_with_result_shows_success(self) -> None:
        """Test that when first step has a result, it shows SUCCESS and overall status is NOT_STARTED."""
        state = create_test_release_state()
        model = DisplayModelGeneric()

        package = state.packages["test-package"]
        workflow = package.build

        # Modify state to give first step a result
        package.meta.ref = "boo"

        status, steps = model.get_workflow_status(package, workflow)

        # Overall status should still be NOT_STARTED (second step not started)
        assert status == StepStatus.NOT_STARTED

        # Should have at least two steps
        assert len(steps) >= 2

        # Find the actual Step objects (filter out Section objects if any)
        step_objects = [s for s in steps if isinstance(s, Step)]
        assert len(step_objects) >= 2

        # First step should be SUCCESS (has result)
        assert step_objects[0].status == StepStatus.SUCCEEDED

        # Second step should be NOT_STARTED
        assert step_objects[1].status == StepStatus.NOT_STARTED
