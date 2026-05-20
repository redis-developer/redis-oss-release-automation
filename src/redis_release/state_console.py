from enum import Enum
from typing import List, Optional, Union

from rich.console import Console
from rich.table import Table

from redis_release.bht.state import Package, ReleaseState, Workflow
from redis_release.state_display import Section, Step, StepStatus, get_display_model
from redis_release.state_slack import get_workflow_link


class StateFormat(str, Enum):
    TABLE = "table"
    TEXT = "text"


class ConsoleStatePrinter:
    """Handles printing of release state to console using Rich tables."""

    def __init__(self, console: Optional[Console] = None):
        """Initialize the printer.

        Args:
            console: Optional Rich Console instance (creates new one if not provided)
        """
        self.console = console or Console()

    def print_state(
        self,
        state: ReleaseState,
        format: StateFormat = StateFormat.TABLE,
    ) -> None:
        """Print the release state in the requested format."""
        if format == StateFormat.TEXT:
            self._print_state_text(state)
        else:
            self._print_state_table(state)

    def _print_state_table(self, state: ReleaseState) -> None:
        """Print table showing the release state.

        Args:
            state: The ReleaseState to display
        """
        table = Table(
            title=f"[bold cyan]Release State: {state.meta.tag or 'N/A'}[/bold cyan]",
            show_header=True,
            show_lines=True,
            header_style="bold magenta",
            border_style="bright_blue",
            title_style="bold cyan",
        )

        table.add_column("Package", style="cyan", no_wrap=True, min_width=20, width=20)
        table.add_column(
            "Build", justify="center", no_wrap=True, min_width=20, width=20
        )
        table.add_column(
            "Publish", justify="center", no_wrap=True, min_width=20, width=20
        )
        table.add_column("Details", style="yellow", width=100)

        for package_name, package in sorted(state.packages.items()):
            build_status = self.get_workflow_status_display(package, package.build)

            publish_status = ""
            if package.publish is not None:
                publish_status = self.get_workflow_status_display(
                    package, package.publish
                )

            # Collect details from workflows
            details = self.collect_details(package)

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

    def collect_text_details(self, steps: List[Union[Step, Section]]) -> List[str]:
        """Collect text details from steps list.

        The first item in the steps list should be a Section, which will be used as the header.

        Args:
            steps: List of Step and Section objects (first item should be Section)

        Returns:
            List of formatted strings
        """
        details: List[str] = []
        indent = " " * 2

        for item in steps:
            if isinstance(item, Section):
                # Section is used as the header
                details.append(item.name)
            elif isinstance(item, Step):
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
        if build_status[0] != StepStatus.NOT_STARTED:
            details.extend(self.collect_text_details(build_status[1]))
        if package.publish is not None:
            publish_status = display_model.get_workflow_status(package, package.publish)
            if publish_status[0] != StepStatus.NOT_STARTED:
                details.extend(self.collect_text_details(publish_status[1]))

        return "\n".join(details)

    def _print_state_text(self, state: ReleaseState) -> None:
        """Print the release state as plain (Rich-styled) text, no table."""
        self.console.print()
        self.console.print(
            f"[bold cyan]Release State: {state.meta.tag or 'N/A'}[/bold cyan]"
        )
        self.console.print()

        for package_name, package in sorted(state.packages.items()):
            display_model = get_display_model(package.meta)
            build_status = display_model.get_workflow_status(package, package.build)
            publish_status = None
            if package.publish is not None:
                publish_status = display_model.get_workflow_status(
                    package, package.publish
                )

            # Package-level state: prefer publish if it has progressed, else build.
            overall = (
                publish_status[0]
                if publish_status is not None
                and publish_status[0] != StepStatus.NOT_STARTED
                else build_status[0]
            )
            if overall == StepStatus.NOT_STARTED:
                continue
            self.console.print(
                f"[bold]{package_name}[/bold]  {self.get_step_status_display(overall)}"
            )

            repo = package.meta.repo
            self.console.print(
                f"  build   {self.get_step_status_display(build_status[0])}"
                f"{self._url_suffix(repo, package.build, build_status[0])}"
            )
            if package.publish is not None and publish_status is not None:
                self.console.print(
                    f"  publish {self.get_step_status_display(publish_status[0])}"
                    f"{self._url_suffix(repo, package.publish, publish_status[0])}"
                )

            details: List[str] = []
            if build_status[0] not in (StepStatus.NOT_STARTED, StepStatus.SUCCEEDED):
                details.extend(self.collect_text_details(build_status[1]))
            if publish_status is not None and publish_status[0] not in (
                StepStatus.NOT_STARTED,
                StepStatus.SUCCEEDED,
            ):
                details.extend(self.collect_text_details(publish_status[1]))

            if details:
                self.console.print("  details")
                for line in details:
                    self.console.print(f"    {line}")

            self.console.print()

    @staticmethod
    def _url_suffix(repo: str, workflow: Workflow, status: StepStatus) -> str:
        if status == StepStatus.NOT_STARTED:
            return ""
        link = workflow.url or get_workflow_link(repo, workflow.run_id)
        return f"  {link}" if link else ""


def print_state(
    state: ReleaseState,
    format: StateFormat = StateFormat.TABLE,
    console: Optional[Console] = None,
) -> None:
    """Print the release state in the requested format.

    Args:
        state: The ReleaseState to display
        format: Output format (table or text)
        console: Optional Rich Console instance (creates new one if not provided)
    """
    printer = ConsoleStatePrinter(console)
    printer.print_state(state, format=format)
