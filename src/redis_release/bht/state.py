import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from redis_release.models import (
    PackageType,
    ReleaseType,
    WorkflowConclusion,
    WorkflowStatus,
    WorkflowType,
)

from ..config import Config

logger = logging.getLogger(__name__)


class WorkflowEphemeral(BaseModel):
    """Ephemeral workflow state. Reset on each run.

    Each workflow step has a pair of fields indicating the step status:
    One ephemeral field is set when the step is attempted. It may have three states:
    - `None` (default): Step has not been attempted
    - `True`: Step has been attempted and failed
    - `False`: Step has been attempted and succeeded

    Ephemeral fields are reset on each run. Their values are persisted but only until
    next run is started.
    So they indicate either current (if run is in progress) or last run state.

    The other field indicates the step result, it may either have some value or be empty.

    For example for trigger step we have `trigger_failed` ephemeral
    and `triggered_at` result fields.

    Each step may be in one of the following states:
        Not started
        Failed
        Succeeded or OK
        Incorrect (this shouln't happen)

    The following decision table show how step status is determined for trigger step.
    In general this logic is used to display release state table.

    tigger_failed -> | None (default) |   True    |   False   |
    triggered_at:    |                |           |           |
       None          |   Not started  |   Failed  | Incorrect |
      Has value      |       OK       | Incorrect |     OK    |

    """

    trigger_failed: Optional[bool] = None
    trigger_attempted: Optional[bool] = None
    identify_failed: Optional[bool] = None
    timed_out: Optional[bool] = None
    artifacts_download_failed: Optional[bool] = None
    extract_result_failed: Optional[bool] = None
    log_once_flags: Dict[str, bool] = Field(default_factory=dict, exclude=True)


class Workflow(BaseModel):
    workflow_type: Optional[WorkflowType] = None
    workflow_file: str = ""
    inputs: Dict[str, str] = Field(default_factory=dict)
    uuid: Optional[str] = None
    triggered_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    run_id: Optional[int] = None
    url: Optional[str] = None
    timeout_minutes: int = 45
    status: Optional[WorkflowStatus] = None
    conclusion: Optional[WorkflowConclusion] = None
    artifacts: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    ephemeral: WorkflowEphemeral = Field(default_factory=WorkflowEphemeral)


class PackageMetaEphemeral(BaseModel):
    """Ephemeral package metadata. Reset on each run.

    See WorkflowEphemeral for more details.
    """

    force_rebuild: bool = False
    identify_ref_failed: bool = False
    log_once_flags: Dict[str, bool] = Field(default_factory=dict, exclude=True)


class PackageMeta(BaseModel):
    """Metadata for a package."""

    package_type: Optional[PackageType] = None
    repo: str = ""
    ref: Optional[str] = None
    publish_internal_release: bool = False
    ephemeral: PackageMetaEphemeral = Field(default_factory=PackageMetaEphemeral)


class Package(BaseModel):
    """State for a package in the release."""

    meta: PackageMeta = Field(default_factory=PackageMeta)
    build: Workflow = Field(default_factory=Workflow)
    publish: Workflow = Field(default_factory=Workflow)


class ReleaseMetaEphemeral(BaseModel):
    """Ephemeral release metadata. Reset on each run.

    See WorkflowEphemeral for more details.
    """

    log_once_flags: Dict[str, bool] = Field(default_factory=dict, exclude=True)


class ReleaseMeta(BaseModel):
    """Metadata for the release."""

    tag: Optional[str] = None
    release_type: Optional[ReleaseType] = None
    ephemeral: ReleaseMetaEphemeral = Field(default_factory=ReleaseMetaEphemeral)


class ReleaseState(BaseModel):
    """Release state adapted for behavior tree usage."""

    meta: ReleaseMeta = Field(default_factory=ReleaseMeta)
    packages: Dict[str, Package] = Field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Config) -> "ReleaseState":
        """Build ReleaseState from config with default values."""
        packages = {}
        for package_name, package_config in config.packages.items():
            if not isinstance(package_config.package_type, PackageType):
                raise ValueError(
                    f"Package '{package_name}': package_type must be a PackageType, "
                    f"got {type(package_config.package_type).__name__}"
                )
            # Validate and get build workflow file
            if not isinstance(package_config.build_workflow, str):
                raise ValueError(
                    f"Package '{package_name}': build_workflow must be a string, "
                    f"got {type(package_config.build_workflow).__name__}"
                )
            if not package_config.build_workflow.strip():
                raise ValueError(
                    f"Package '{package_name}': build_workflow cannot be empty"
                )

            # Validate and get publish workflow file
            if not isinstance(package_config.publish_workflow, str):
                raise ValueError(
                    f"Package '{package_name}': publish_workflow must be a string, "
                    f"got {type(package_config.publish_workflow).__name__}"
                )
            if not package_config.publish_workflow.strip():
                raise ValueError(
                    f"Package '{package_name}': publish_workflow cannot be empty"
                )

            # Initialize package metadata
            package_meta = PackageMeta(
                repo=package_config.repo,
                ref=package_config.ref,
                package_type=package_config.package_type,
                publish_internal_release=package_config.publish_internal_release,
            )

            # Initialize build workflow
            build_workflow = Workflow(
                workflow_type=WorkflowType.BUILD,
                workflow_file=package_config.build_workflow,
                inputs=package_config.build_inputs.copy(),
                timeout_minutes=package_config.build_timeout_minutes,
            )

            # Initialize publish workflow
            publish_workflow = Workflow(
                workflow_type=WorkflowType.PUBLISH,
                workflow_file=package_config.publish_workflow,
                inputs=package_config.publish_inputs.copy(),
                timeout_minutes=package_config.publish_timeout_minutes,
            )

            # Create package state with initialized workflows
            packages[package_name] = Package(
                meta=package_meta,
                build=build_workflow,
                publish=publish_workflow,
            )

        return cls(packages=packages)

    @classmethod
    def from_json(cls, data: Union[str, Dict, Path]) -> "ReleaseState":
        """Load ReleaseState from JSON string, dict, or file path."""
        if isinstance(data, Path):
            with open(data, "r") as f:
                json_data = json.load(f)
        elif isinstance(data, str):
            json_data = json.loads(data)
        else:
            json_data = data

        return cls(**json_data)


def reset_model_to_defaults(target: BaseModel, default: BaseModel) -> None:
    """Recursively reset a BaseModel in-place with values from default model."""
    for field_name, field_info in default.model_fields.items():
        default_value = getattr(default, field_name)

        if isinstance(default_value, BaseModel):
            # Recursive case: field is a BaseModel
            target_value = getattr(target, field_name)
            if isinstance(target_value, BaseModel):
                reset_model_to_defaults(target_value, default_value)
            else:
                raise TypeError(
                    f"Field '{field_name}' type mismatch: expected {type(default_value)}, got {type(target_value)}"
                )
        else:
            # Base case: field is not a BaseModel, copy the value
            if isinstance(default_value, (list, dict, set)):
                # Deep copy collections
                import copy

                setattr(target, field_name, copy.deepcopy(default_value))
            else:
                # Simple value, copy directly
                setattr(target, field_name, default_value)


def print_state_table(state: ReleaseState, console: Optional[Console] = None) -> None:
    """Print table showing the release state.

    Args:
        state: The ReleaseState to display
        console: Optional Rich Console instance (creates new one if not provided)
    """
    if console is None:
        console = Console()

    # Create table with title
    table = Table(
        title=f"[bold cyan]Release State: {state.meta.tag or 'N/A'}[/bold cyan]",
        show_header=True,
        header_style="bold magenta",
        border_style="bright_blue",
        title_style="bold cyan",
    )

    # Add columns
    table.add_column("Package", style="cyan", no_wrap=True, width=20)
    table.add_column("Build", justify="center", width=15)
    table.add_column("Publish", justify="center", width=15)
    table.add_column("Details", style="yellow", width=40)

    # Process each package
    for package_name, package in sorted(state.packages.items()):
        # Determine build status
        build_status = _get_workflow_status_display(package.build)

        # Determine publish status
        publish_status = _get_workflow_status_display(package.publish)

        # Collect details from workflows
        details = _collect_details(package)

        # Add row to table
        table.add_row(
            package_name,
            build_status,
            publish_status,
            details,
        )

    # Print the table
    console.print()
    console.print(table)
    console.print()


def _get_workflow_status_display(workflow: Workflow) -> str:
    """Get a rich-formatted status display for a workflow.

    Args:
        workflow: The workflow to check

    Returns:
        Rich-formatted status string
    """
    # Check result field - if we have result, we succeeded
    if workflow.result is not None:
        return "[bold green]✓ Success[/bold green]"

    # Check if workflow was triggered
    if workflow.triggered_at is None:
        return "[dim]− Not Started[/dim]"

    # Workflow was triggered but no result - it failed
    return "[bold red]✗ Failed[/bold red]"


def _collect_workflow_details(workflow: Workflow, prefix: str) -> List[str]:
    """Collect details from a workflow using bottom-up approach.

    Shows successes until the first failure, then stops.
    Bottom-up means: trigger → identify → timeout → conclusion → artifacts → result

    Args:
        workflow: The workflow to check
        prefix: Prefix for detail messages (e.g., "Build" or "Publish")

    Returns:
        List of detail strings
    """
    details: List[str] = []

    # Stage 1: Trigger (earliest/bottom)
    if workflow.ephemeral.trigger_failed or workflow.triggered_at is None:
        details.append(f"[red]✗ Trigger {prefix} workflow failed[/red]")
        return details
    else:
        details.append(f"[green]✓ {prefix} workflow triggered[/green]")

    # Stage 2: Identify
    if workflow.ephemeral.identify_failed or workflow.run_id is None:
        details.append(f"[red]✗ {prefix} workflow not found[/red]")
        return details
    else:
        details.append(f"[green]✓ {prefix} workflow found[/green]")

    # Stage 3: Timeout (only ephemeral)
    if workflow.ephemeral.timed_out:
        details.append(f"[yellow]⏱ {prefix} timed out[/yellow]")
        return details

    # Stage 4: Workflow conclusion
    if workflow.conclusion == WorkflowConclusion.FAILURE:
        details.append(f"[red]✗ {prefix} workflow failed[/red]")
        return details

    # Stage 5: Artifacts download
    if workflow.ephemeral.artifacts_download_failed or workflow.artifacts is None:
        details.append(f"[red]✗ {prefix} artifacts download failed[/red]")
        return details
    else:
        details.append(f"[green]✓ {prefix} artifacts downloaded[/green]")

    # Stage 6: Result extraction (latest/top)
    if workflow.result is None or workflow.ephemeral.extract_result_failed:
        details.append(f"[red]✗ {prefix} failed to extract result[/red]")
        return details
    else:
        details.append(f"[green]✓ {prefix} result extracted[/green]")

    # Check for other workflow states
    if workflow.status == WorkflowStatus.IN_PROGRESS:
        details.append(f"[blue]⟳ {prefix} in progress[/blue]")
    elif workflow.status == WorkflowStatus.QUEUED:
        details.append(f"[cyan]⋯ {prefix} queued[/cyan]")
    elif workflow.status == WorkflowStatus.PENDING:
        details.append(f"[dim]○ {prefix} pending[/dim]")

    return details


def _collect_package_details(package: Package) -> List[str]:
    """Collect details from package metadata.

    Args:
        package: The package to check

    Returns:
        List of detail strings (may be empty)
    """
    details: List[str] = []

    if package.meta.ephemeral.identify_ref_failed:
        details.append("[red]✗ Identify target ref to run workflow failed[/red]")
    elif package.meta.ref is not None:
        details.append(f"[green]✓ Target Ref identified: {package.meta.ref}[/green]")

    return details


def _collect_details(package: Package) -> str:
    """Collect and format all details from package and workflows.

    Args:
        package: The package to check

    Returns:
        Formatted string of details
    """
    details: List[str] = []

    # Collect package-level details
    details.extend(_collect_package_details(package))

    # Collect build workflow details
    details.extend(_collect_workflow_details(package.build, "Build"))

    # Only collect publish details if build succeeded (has result)
    if package.build.result is not None:
        details.extend(_collect_workflow_details(package.publish, "Publish"))

    return "\n".join(details)
