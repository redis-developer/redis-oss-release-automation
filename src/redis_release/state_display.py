"""Console display utilities for release state."""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple, Union

from py_trees import common
from py_trees.common import Status
from rich.console import Console
from rich.table import Table

from redis_release.models import WorkflowConclusion

from .bht.state import (
    HomebrewMeta,
    HomebrewMetaEphemeral,
    Package,
    PackageMeta,
    ReleaseState,
    SnapMeta,
    SnapMetaEphemeral,
    Workflow,
)
from .models import PackageType


# See WorkflowEphemeral for more details on the flags and steps
class StepStatus(str, Enum):
    """Status of a workflow step."""

    NOT_STARTED = "not_started"
    RUNNING = "in_progress"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    INCORRECT = "incorrect"


@dataclass
class Step:
    status: StepStatus = StepStatus.INCORRECT
    name: str = ""
    message: Optional[str] = None
    has_result: bool = False
    ephemeral_status: Optional[Status] = None


@dataclass
class Section:
    name: str


# Decision table for step status
# See WorkflowEphemeral for more details on the flags
_STEP_STATUS_MAPPING = {
    None: {False: StepStatus.NOT_STARTED, True: StepStatus.SUCCEEDED},
    Status.RUNNING: {False: StepStatus.RUNNING},
    Status.FAILURE: {False: StepStatus.FAILED},
    Status.SUCCESS: {True: StepStatus.SUCCEEDED},
}


class DisplayModel:
    """Model for computing display status from workflow state."""

    def get_step_status(
        self, step_result: bool, step_status_flag: Optional[common.Status]
    ) -> StepStatus:
        """Get step status based on result and ephemeral flag.

        See WorkflowEphemeral for more details on the flags.

        Args:
            step_result: Whether the step has a result
            step_status_flag: The ephemeral status flag value

        Returns:
            The determined step status
        """
        if step_status_flag in _STEP_STATUS_MAPPING:
            if step_result in _STEP_STATUS_MAPPING[step_status_flag]:
                return _STEP_STATUS_MAPPING[step_status_flag][step_result]
        return StepStatus.INCORRECT

    def get_workflow_status(
        self, package: Package, workflow: Workflow
    ) -> Tuple[StepStatus, List[Union[Step, Section]]]:
        """Get workflow status based on ephemeral and result fields.

        Returns tuple of overall status and list of steps.

        See WorkflowEphemeral for more details on the flags.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Tuple of (overall_status, list of Step objects)
        """
        steps_status: List[Union[Step, Section]] = []
        steps = [
            Step(
                name="Identify target ref",
                has_result=package.meta.ref is not None,
                ephemeral_status=package.meta.ephemeral.identify_ref,
            ),
            Step(
                name="Trigger workflow",
                has_result=workflow.triggered_at is not None,
                ephemeral_status=workflow.ephemeral.trigger_workflow,
            ),
            Step(
                name="Find workflow run",
                has_result=workflow.run_id is not None,
                ephemeral_status=workflow.ephemeral.identify_workflow,
            ),
            Step(
                name="Wait for completion",
                has_result=workflow.conclusion == WorkflowConclusion.SUCCESS,
                ephemeral_status=workflow.ephemeral.wait_for_completion,
                message=workflow.ephemeral.wait_for_completion_message,
            ),
            Step(
                name="Download artifacts",
                has_result=workflow.artifacts is not None,
                ephemeral_status=workflow.ephemeral.download_artifacts,
            ),
            Step(
                name="Get result",
                has_result=workflow.result is not None,
                ephemeral_status=workflow.ephemeral.extract_artifact_result,
            ),
        ]
        for step in steps:
            step.status = self.get_step_status(step.has_result, step.ephemeral_status)
            steps_status.append(step)
            if step.status != StepStatus.SUCCEEDED:
                return (step.status, steps_status)
        return (StepStatus.SUCCEEDED, steps_status)

    def get_release_validation_status(
        self, meta: Union[HomebrewMeta, SnapMeta]
    ) -> Tuple[StepStatus, List[Union[Step, Section]]]:
        """Get release validation status for Homebrew or Snap packages.

        This method checks validation steps specific to Homebrew and Snap packages,
        such as remote version classification.

        Args:
            meta: The package metadata (HomebrewMeta or SnapMeta)

        Returns:
            Tuple of (overall_status, list of Step objects)
        """
        steps_status: List[Union[Step, Section]] = []
        steps = [
            Step(
                name="Classify remote versions",
                has_result=meta.remote_version is not None,
                ephemeral_status=meta.ephemeral.classify_remote_versions,
            ),
        ]
        for step in steps:
            step.status = self.get_step_status(step.has_result, step.ephemeral_status)
            steps_status.append(step)
            if step.status != StepStatus.SUCCEEDED:
                return (step.status, steps_status)
        return (StepStatus.SUCCEEDED, steps_status)


def get_display_model(package_meta: PackageMeta) -> DisplayModel:
    """Factory function to get the appropriate DisplayModel for a package.

    Args:
        package_meta: The package metadata

    Returns:
        DisplayModel instance appropriate for the package type
    """
    # For now, all package types use the same DisplayModel
    # In the future, this can return specialized DisplayModel subclasses
    # based on package_meta.package_type
    return DisplayModel()


class ConsoleStatePrinter:
    """Handles printing of release state to console using Rich tables."""

    def __init__(self, console: Optional[Console] = None):
        """Initialize the printer.

        Args:
            console: Optional Rich Console instance (creates new one if not provided)
        """
        self.console = console or Console()

    def print_state_table(self, state: ReleaseState) -> None:
        """Print table showing the release state.

        Args:
            state: The ReleaseState to display
        """
        # Create table with title
        table = Table(
            title=f"[bold cyan]Release State: {state.meta.tag or 'N/A'}[/bold cyan]",
            show_header=True,
            show_lines=True,
            header_style="bold magenta",
            border_style="bright_blue",
            title_style="bold cyan",
        )

        # Add columns
        table.add_column("Package", style="cyan", no_wrap=True, min_width=20, width=20)
        table.add_column(
            "Build", justify="center", no_wrap=True, min_width=20, width=20
        )
        table.add_column(
            "Publish", justify="center", no_wrap=True, min_width=20, width=20
        )
        table.add_column("Details", style="yellow", width=100)

        # Process each package
        for package_name, package in sorted(state.packages.items()):
            # Get display model for this package
            display_model = get_display_model(package.meta)

            # Determine build status
            build_status = ""
            if (
                type(package.meta.ephemeral) == HomebrewMetaEphemeral
                or type(package.meta.ephemeral) == SnapMetaEphemeral
            ):
                # to avoid creating new column validation status is counted as part of build workflow
                status, _ = display_model.get_release_validation_status(package.meta)  # type: ignore
                if status != StepStatus.SUCCEEDED:
                    build_status = self.get_step_status_display(status)
                else:
                    build_status = self.get_workflow_status_display(
                        package, package.build
                    )
            else:
                build_status = self.get_workflow_status_display(package, package.build)

            publish_status = ""
            if package.publish is not None:
                # Determine publish status
                publish_status = self.get_workflow_status_display(
                    package, package.publish
                )

            # Collect details from workflows
            details = self.collect_details(package)

            # Add row to table
            table.add_row(
                package_name,
                build_status,
                publish_status,
                details,
            )

        # Print the table
        self.console.print()
        self.console.print(table)
        self.console.print()

    def get_workflow_status_display(self, package: Package, workflow: Workflow) -> str:
        """Get a rich-formatted status display for a workflow.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Rich-formatted status string
        """
        display_model = get_display_model(package.meta)
        workflow_status = display_model.get_workflow_status(package, workflow)
        return self.get_step_status_display(workflow_status[0])

    def get_step_status_display(self, step_status: StepStatus) -> str:
        if step_status == StepStatus.SUCCEEDED:
            return "[bold green]✓ Success[/bold green]"
        elif step_status == StepStatus.RUNNING:
            return "[bold yellow]⏳ In Progress[/bold yellow]"
        elif step_status == StepStatus.NOT_STARTED:
            return "[dim]Not Started[/dim]"
        elif step_status == StepStatus.INCORRECT:
            return "[bold red]✗ Invalid state![/bold red]"

        return "[bold red]✗ Failed[/bold red]"

    def collect_text_details(
        self, steps: List[Union[Step, Section]], prefix: str
    ) -> List[str]:
        details: List[str] = []

        details.append(f"{prefix}")
        indent = " " * 2

        for item in steps:
            if isinstance(item, Step):
                if item.status == StepStatus.SUCCEEDED:
                    details.append(f"{indent}[green]✓ {item.name}[/green]")
                elif item.status == StepStatus.RUNNING:
                    details.append(f"{indent}[yellow]⏳ {item.name}[/yellow]")
                elif item.status == StepStatus.NOT_STARTED:
                    details.append(f"{indent}[dim]Not started: {item.name}[/dim]")
                else:
                    msg = f" ({item.message})" if item.message else ""
                    details.append(f"{indent}[red]✗ {item.name} failed[/red]{msg}")
                    break

        return details

    def collect_details(self, package: Package) -> str:
        """Collect and format all details from package and workflows.

        Args:
            package: The package to check

        Returns:
            Formatted string of details
        """
        details: List[str] = []
        display_model = get_display_model(package.meta)

        build_status = display_model.get_workflow_status(package, package.build)
        if (
            type(package.meta.ephemeral) == HomebrewMetaEphemeral
            or type(package.meta.ephemeral) == SnapMetaEphemeral
        ):
            validation_status, validation_steps = (
                display_model.get_release_validation_status(package.meta)  # type: ignore
            )
            # Show any validation steps only when build has started or validation has failed
            if (
                validation_status != StepStatus.NOT_STARTED
                and build_status[0] != StepStatus.NOT_STARTED
            ) or (validation_status == StepStatus.FAILED):
                details.extend(
                    self.collect_text_details(validation_steps, "Release Validation")
                )

        build_status = display_model.get_workflow_status(package, package.build)
        if build_status[0] != StepStatus.NOT_STARTED:
            details.extend(self.collect_text_details(build_status[1], "Build Workflow"))
        if package.publish is not None:
            publish_status = display_model.get_workflow_status(package, package.publish)
            if publish_status[0] != StepStatus.NOT_STARTED:
                details.extend(
                    self.collect_text_details(publish_status[1], "Publish Workflow")
                )

        return "\n".join(details)


def print_state_table(state: ReleaseState, console: Optional[Console] = None) -> None:
    """Print table showing the release state.

    This is a convenience function that creates a ConsoleStatePrinter and prints the state.

    Args:
        state: The ReleaseState to display
        console: Optional Rich Console instance (creates new one if not provided)
    """
    printer = ConsoleStatePrinter(console)
    printer.print_state_table(state)
