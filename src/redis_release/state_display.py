"""Console display utilities for release state."""

from enum import Enum
from typing import List, Optional, Tuple

from py_trees import common
from py_trees.common import Status
from rich.console import Console
from rich.table import Table

from redis_release.models import WorkflowConclusion

from .bht.state import Package, ReleaseState, Workflow


# See WorkflowEphemeral for more details on the flags and steps
class StepStatus(str, Enum):
    """Status of a workflow step."""

    NOT_STARTED = "not_started"
    RUNNING = "in_progress"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    INCORRECT = "incorrect"


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

    @staticmethod
    def get_step_status(
        step_result: bool, step_status_flag: Optional[common.Status]
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

    @staticmethod
    def get_workflow_status(
        package: Package, workflow: Workflow
    ) -> Tuple[StepStatus, List[Tuple[StepStatus, str, Optional[str]]]]:
        """Get workflow status based on ephemeral and result fields.

        Returns tuple of overall status and list of step statuses.

        See WorkflowEphemeral for more details on the flags.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Tuple of (overall_status, list of (step_status, step_name, step_message))
        """
        steps_status: List[Tuple[StepStatus, str, Optional[str]]] = []
        steps = [
            (
                package.meta.ref is not None,
                package.meta.ephemeral.identify_ref,
                "Identify target ref",
                None,
            ),
            (
                workflow.triggered_at is not None,
                workflow.ephemeral.trigger_workflow,
                "Trigger workflow",
                None,
            ),
            (
                workflow.run_id is not None,
                workflow.ephemeral.identify_workflow,
                "Find workflow run",
                None,
            ),
            (
                workflow.conclusion == WorkflowConclusion.SUCCESS,
                workflow.ephemeral.wait_for_completion,
                "Wait for completion",
                workflow.ephemeral.wait_for_completion_message,
            ),
            (
                workflow.artifacts is not None,
                workflow.ephemeral.download_artifacts,
                "Download artifacts",
                None,
            ),
            (
                workflow.result is not None,
                workflow.ephemeral.extract_artifact_result,
                "Get result",
                None,
            ),
        ]
        for result, status_flag, name, status_msg in steps:
            s = DisplayModel.get_step_status(result, status_flag)
            steps_status.append((s, name, status_msg))
            if s != StepStatus.SUCCEEDED:
                return (s, steps_status)
        return (StepStatus.SUCCEEDED, steps_status)


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
            # Determine build status
            build_status = self.get_workflow_status_display(package, package.build)

            # Determine publish status
            publish_status = self.get_workflow_status_display(package, package.publish)

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
        workflow_status = DisplayModel.get_workflow_status(package, workflow)
        if workflow_status[0] == StepStatus.SUCCEEDED:
            return "[bold green]✓ Success[/bold green]"
        elif workflow_status[0] == StepStatus.RUNNING:
            return "[bold yellow]⏳ In Progress[/bold yellow]"
        elif workflow_status[0] == StepStatus.NOT_STARTED:
            return "[dim]Not Started[/dim]"
        elif workflow_status[0] == StepStatus.INCORRECT:
            return "[bold red]✗ Invalid state![/bold red]"

        return "[bold red]✗ Failed[/bold red]"

    def collect_workflow_details(
        self, package: Package, workflow: Workflow, prefix: str
    ) -> List[str]:
        """Collect details from a workflow using bottom-up approach.

        Shows successes until the first failure, then stops.
        Bottom-up means: trigger → identify → timeout → conclusion → artifacts → result

        Args:
            package: The package containing the workflow
            workflow: The workflow to check
            prefix: Prefix for detail messages (e.g., "Build" or "Publish")

        Returns:
            List of detail strings
        """
        details: List[str] = []

        workflow_status = DisplayModel.get_workflow_status(package, workflow)
        if workflow_status[0] == StepStatus.NOT_STARTED:
            return details

        details.append(f"{prefix} Workflow")
        indent = " " * 2

        for step_status, step_name, step_message in workflow_status[1]:
            if step_status == StepStatus.SUCCEEDED:
                details.append(f"{indent}[green]✓ {step_name}[/green]")
            elif step_status == StepStatus.RUNNING:
                details.append(f"{indent}[yellow]⏳ {step_name}[/yellow]")
            elif step_status == StepStatus.NOT_STARTED:
                details.append(f"{indent}[dim]Not started: {step_name}[/dim]")
            else:
                msg = f" ({step_message})" if step_message else ""
                details.append(f"{indent}[red]✗ {step_name} failed[/red]{msg}")
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

        details.extend(self.collect_workflow_details(package, package.build, "Build"))
        details.extend(
            self.collect_workflow_details(package, package.publish, "Publish")
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
