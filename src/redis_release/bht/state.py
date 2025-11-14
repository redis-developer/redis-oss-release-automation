import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from py_trees import common
from py_trees.common import Status
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

SUPPORTED_STATE_VERSION = 2


class WorkflowEphemeral(BaseModel):
    """Ephemeral workflow state. Reset on each run.

    The main purpose of ephemeral fields is to prevent retry loops and to allow extensive status reporting.

    Each workflow step has a pair of fields indicating the step status:
    One ephemeral field is set when the step is attempted. It may have four states:
    - `None` (default): Step has not been attempted
    - `common.Status.RUNNING`: Step is currently running
    - `common.Status.FAILURE`: Step has been attempted and failed
    - `common.Status.SUCCESS`: Step has been attempted and succeeded

    Ephemeral fields are reset on each run. Their values are persisted but only until
    next run is started.
    So they indicate either current (if run is in progress) or last run state.

    The other field indicates the step result, it may either have some value or be empty.

    For example for trigger step we have `trigger_workflow` ephemeral
    and `triggered_at` result fields.

    Optional message field may be used to provide additional information about the step.
    For example wait_for_completion_message may contain information about timeout.

    Given combination of ephemeral and result fields we can determine step status.
    Each step may be in one of the following states:
        Not started
        Failed
        Succeeded or OK
        Incorrect (this shouln't happen)

    The following decision table show how step status is determined for trigger step.
    In general this is applicable to all steps.

    tigger_workflow -> | None (default) |     Running    |   Failure   |  Success   |
    triggered_at:      |                |                |             |            |
       None            |   Not started  |   In progress  |    Failed   |  Incorrect |
      Has value        |       OK       |    Incorrect   |  Incorrect  |     OK     |

    The result field (triggered_at in this case) should not be set while step is
    running, if step was not started or if it's failed.
    And it should be set if trigger_workflow is successful.
    It may be set if trigger_workflow is None, which is the case when release
    process was restarted and all ephemeral fields are reset, but the particular
    step was successful in previous run.

    Correct values are not eforced it's up to the implementation to correctly
    set the fields.
    """

    identify_workflow: Optional[common.Status] = None
    trigger_workflow: Optional[common.Status] = None
    wait_for_completion: Optional[common.Status] = None
    wait_for_completion_message: Optional[str] = None
    download_artifacts: Optional[common.Status] = None
    extract_artifact_result: Optional[common.Status] = None

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
    identify_ref: Optional[common.Status] = None
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

    version: int = 2
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

        if json_data.get("version") != SUPPORTED_STATE_VERSION:
            raise ValueError(
                f"Unsupported state version: {json_data.get('version')}, "
                f"expected: {SUPPORTED_STATE_VERSION}"
            )
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
        show_lines=True,
        header_style="bold magenta",
        border_style="bright_blue",
        title_style="bold cyan",
    )

    # Add columns
    table.add_column("Package", style="cyan", no_wrap=True, min_width=20, width=20)
    table.add_column("Build", justify="center", no_wrap=True, min_width=20, width=20)
    table.add_column("Publish", justify="center", no_wrap=True, min_width=20, width=20)
    table.add_column("Details", style="yellow", width=100)

    # Process each package
    for package_name, package in sorted(state.packages.items()):
        # Determine build status
        build_status = _get_workflow_status_display(package, package.build)

        # Determine publish status
        publish_status = _get_workflow_status_display(package, package.publish)

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


class StepStatus(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "in_progress"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    INCORRECT = "incorrect"


# Decision table for step status
# See WorkflowEphemeral for more details on the flags
_step_status_mapping = {
    None: {False: StepStatus.NOT_STARTED, True: StepStatus.SUCCEEDED},
    Status.RUNNING: {False: StepStatus.RUNNING},
    Status.FAILURE: {False: StepStatus.FAILED},
    Status.SUCCESS: {True: StepStatus.SUCCEEDED},
}


def _get_step_status(
    step_result: bool, step_status_flag: Optional[common.Status]
) -> StepStatus:
    """Get step status based on result and ephemeral flag.

    See WorkflowEphemeral for more details on the flags.
    """
    if step_status_flag in _step_status_mapping:
        if step_result in _step_status_mapping[step_status_flag]:
            return _step_status_mapping[step_status_flag][step_result]
    return StepStatus.INCORRECT


def _get_workflow_status(
    package: Package, workflow: Workflow
) -> tuple[StepStatus, List[tuple[StepStatus, str, Optional[str]]]]:
    """Get workflow status based on ephemeral and result fields.

    Returns tuple of overall status and list of step statuses.

    See WorkflowEphemeral for more details on the flags.
    """
    steps_status: List[tuple[StepStatus, str, Optional[str]]] = []
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
        s = _get_step_status(result, status_flag)
        steps_status.append((s, name, status_msg))
        if s != StepStatus.SUCCEEDED:
            return (s, steps_status)
    return (StepStatus.SUCCEEDED, steps_status)


def _get_workflow_status_display(package: Package, workflow: Workflow) -> str:
    """Get a rich-formatted status display for a workflow.

    Args:
        package: The package containing the workflow
        workflow: The workflow to check

    Returns:
        Rich-formatted status string
    """
    workflow_status = _get_workflow_status(package, workflow)
    if workflow_status[0] == StepStatus.SUCCEEDED:
        return "[bold green]✓ Success[/bold green]"
    elif workflow_status[0] == StepStatus.RUNNING:
        return "[bold yellow]⏳ In Progress[/bold yellow]"
    elif workflow_status[0] == StepStatus.NOT_STARTED:
        return "[dim]Not Started[/dim]"
    elif workflow_status[0] == StepStatus.INCORRECT:
        return "[bold red]✗ Invalid state![/bold red]"

    return "[bold red]✗ Failed[/bold red]"


def _collect_workflow_details(
    package: Package, workflow: Workflow, prefix: str
) -> List[str]:
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

    workflow_status = _get_workflow_status(package, workflow)
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


def _collect_details(package: Package) -> str:
    """Collect and format all details from package and workflows.

    Args:
        package: The package to check

    Returns:
        Formatted string of details
    """
    details: List[str] = []

    details.extend(_collect_workflow_details(package, package.build, "Build"))
    details.extend(_collect_workflow_details(package, package.publish, "Publish"))

    return "\n".join(details)
