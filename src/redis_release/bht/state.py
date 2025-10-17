import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Union

from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from rich.console import Console
from rich.pretty import pretty_repr
from rich.table import Table

from redis_release.models import (
    PackageType,
    ReleaseType,
    WorkflowConclusion,
    WorkflowStatus,
    WorkflowType,
)
from redis_release.state_manager import S3Backed, logger

from ..config import Config

if TYPE_CHECKING:
    from .args import ReleaseArgs

logger = logging.getLogger(__name__)


class WorkflowEphemeral(BaseModel):
    """Ephemeral workflow state that is not persisted."""

    trigger_failed: bool = False
    trigger_attempted: bool = False
    identify_failed: bool = False
    timed_out: bool = False
    artifacts_download_failed: bool = False
    extract_result_failed: bool = False
    log_once_flags: Dict[str, bool] = Field(default_factory=dict)


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
    ephemeral: WorkflowEphemeral = Field(
        default_factory=WorkflowEphemeral, exclude=True
    )


class PackageMetaEphemeral(BaseModel):
    """Ephemeral package metadata that is not persisted."""

    force_rebuild: bool = False
    identify_ref_failed: bool = False
    log_once_flags: Dict[str, bool] = Field(default_factory=dict)


class PackageMeta(BaseModel):
    """Metadata for a package."""

    package_type: Optional[PackageType] = None
    repo: str = ""
    ref: Optional[str] = None
    publish_internal_release: bool = False
    ephemeral: PackageMetaEphemeral = Field(
        default_factory=PackageMetaEphemeral, exclude=True
    )


class Package(BaseModel):
    """State for a package in the release."""

    meta: PackageMeta = Field(default_factory=PackageMeta)
    build: Workflow = Field(default_factory=Workflow)
    publish: Workflow = Field(default_factory=Workflow)


class ReleaseMetaEphemeral(BaseModel):
    """Ephemeral release metadata that is not persisted."""

    log_once_flags: Dict[str, bool] = Field(default_factory=dict)


class ReleaseMeta(BaseModel):
    """Metadata for the release."""

    tag: Optional[str] = None
    release_type: Optional[ReleaseType] = None
    ephemeral: ReleaseMetaEphemeral = Field(
        default_factory=ReleaseMetaEphemeral, exclude=True
    )


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


class StateStorage(Protocol):
    """Protocol for state storage backends."""

    def get(self, tag: str) -> Optional[dict]:
        """Load state data by tag.

        Args:
            tag: Release tag

        Returns:
            State dict or None if not found
        """
        ...

    def put(self, tag: str, state: dict) -> None:
        """Save state data by tag.

        Args:
            tag: Release tag
            state: State dict to save
        """
        ...

    def acquire_lock(self, tag: str) -> bool:
        """Acquire a lock for the release process.

        Args:
            tag: Release tag

        Returns:
            True if lock acquired successfully
        """
        ...

    def release_lock(self, tag: str) -> bool:
        """Release a lock for the release process.

        Args:
            tag: Release tag

        Returns:
            True if lock released successfully
        """
        ...


class StateSyncer:
    """Syncs ReleaseState to storage backend only when changed.

    Can be used as a context manager to automatically acquire and release locks.
    """

    def __init__(
        self,
        storage: StateStorage,
        config: Config,
        args: "ReleaseArgs",
    ):
        self.tag = args.release_tag
        self.storage = storage
        self.config = config
        self.args = args
        self.last_dump: Optional[str] = None
        self._state: Optional[ReleaseState] = None
        self._lock_acquired = False

    def __enter__(self) -> "StateSyncer":
        """Acquire lock when entering context."""
        if not self.storage.acquire_lock(self.tag):
            raise RuntimeError(f"Failed to acquire lock for tag: {self.tag}")
        self._lock_acquired = True
        logger.info(f"Lock acquired for tag: {self.tag}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Release lock when exiting context."""
        if self._lock_acquired:
            self.storage.release_lock(self.tag)
            self._lock_acquired = False
            logger.info(f"Lock released for tag: {self.tag}")
        print_state_table(self.state)

    @property
    def state(self) -> ReleaseState:
        if self._state is None:
            loaded = self.load()
            if loaded is None:
                self._state = self.default_state()
            else:
                self._state = loaded
                self.apply_args(self._state)
            logger.debug(pretty_repr(self._state))
        return self._state

    def default_state(self) -> ReleaseState:
        """Create default state from config."""
        state = ReleaseState.from_config(self.config)
        self.apply_args(state)
        return state

    def apply_args(self, state: ReleaseState) -> None:
        """Apply arguments to state."""
        state.meta.tag = self.tag

        if self.args:
            if "all" in self.args.force_rebuild:
                # Set force_rebuild for all packages
                for package_name in state.packages:
                    state.packages[package_name].meta.ephemeral.force_rebuild = True
            else:
                # Set force_rebuild for specific packages
                for package_name in self.args.force_rebuild:
                    if package_name in state.packages:
                        state.packages[package_name].meta.ephemeral.force_rebuild = True

    def load(self) -> Optional[ReleaseState]:
        """Load state from storage backend."""
        state_data = self.storage.get(self.tag)
        if state_data is None:
            return None

        state = ReleaseState(**state_data)
        self.last_dump = state.model_dump_json(indent=2)
        return state

    def sync(self) -> None:
        """Save state to storage backend if changed since last sync."""
        current_dump = self.state.model_dump_json(indent=2)

        if current_dump != self.last_dump:
            self.last_dump = current_dump
            state_dict = json.loads(current_dump)
            self.storage.put(self.tag, state_dict)
            logger.debug("State saved")


class InMemoryStateStorage:
    """In-memory state storage for testing."""

    def __init__(self) -> None:
        self._storage: Dict[str, dict] = {}
        self._locks: Dict[str, bool] = {}

    def get(self, tag: str) -> Optional[dict]:
        """Load state data by tag."""
        return self._storage.get(tag)

    def put(self, tag: str, state: dict) -> None:
        """Save state data by tag."""
        self._storage[tag] = state

    def acquire_lock(self, tag: str) -> bool:
        """Acquire a lock for the release process."""
        if self._locks.get(tag, False):
            return False
        self._locks[tag] = True
        return True

    def release_lock(self, tag: str) -> bool:
        """Release a lock for the release process."""
        self._locks[tag] = False
        return True


class S3StateStorage(S3Backed):
    def __init__(
        self,
        bucket_name: Optional[str] = None,
        aws_region: str = "us-east-1",
        aws_profile: Optional[str] = None,
        owner: Optional[str] = None,
    ):
        super().__init__(bucket_name, False, aws_region, aws_profile)
        # Generate UUID for this instance to use as lock owner
        self.owner = owner if owner else str(uuid.uuid4())

    def get(self, tag: str) -> Optional[dict]:
        """Load blackboard data from S3.

        Args:
            tag: Release tag

        Returns:
            ReleaseState object or None if not found
        """
        state_key = f"release-state/{tag}-blackboard.json"
        logger.debug(f"Loading blackboard for tag: {tag}")

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=state_key)
            state_data: dict = json.loads(response["Body"].read().decode("utf-8"))

            logger.debug("Blackboard loaded successfully")

            return state_data

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.debug(f"No existing blackboard found for tag: {tag}")
                return None
            else:
                logger.error(f"Failed to load blackboard: {e}")
                raise

    def put(self, tag: str, state: dict) -> None:
        """Save release state to S3.

        Args:
            state: ReleaseState object to save
        """
        state_key = f"release-state/{tag}-blackboard.json"
        logger.debug(f"Saving blackboard for tag: {tag}")

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        state_json = json.dumps(state, indent=2, default=str)

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=state_key,
                Body=state_json,
                ContentType="application/json",
                Metadata={
                    "tag": tag,
                },
            )

            logger.debug("Blackboard saved successfully")

        except ClientError as e:
            logger.error(f"Failed to save blackboard: {e}")
            raise

    def acquire_lock(self, tag: str) -> bool:
        """Acquire a lock for the release process.

        Args:
            tag: Release tag

        Returns:
            True if lock acquired successfully
        """
        lock_key = f"release-locks/{tag}.lock"
        logger.debug(f"Acquiring lock for tag: {tag}")

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        lock_data = {
            "tag": tag,
            "owner": self.owner,
            "acquired_at": datetime.now().isoformat(),
        }

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=lock_key,
                Body=json.dumps(lock_data, indent=2),
                ContentType="application/json",
                # fail if object already exists
                IfNoneMatch="*",
            )

            logger.debug("Lock acquired successfully")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "PreconditionFailed":
                try:
                    response = self.s3_client.get_object(
                        Bucket=self.bucket_name, Key=lock_key
                    )
                    existing_lock = json.loads(response["Body"].read().decode("utf-8"))
                    logger.warning(
                        f"Lock already held by: {existing_lock.get('owner', 'unknown')}, "
                        f"acquired at: {existing_lock.get('acquired_at', 'unknown')}"
                    )
                except:
                    logger.warning("Lock exists but couldn't read details")
                return False
            else:
                logger.error(f"Failed to acquire lock: {e}")
                raise

    def release_lock(self, tag: str) -> bool:
        """Release a lock for the release process.

        Args:
            tag: Release tag

        Returns:
            True if lock released successfully
        """
        lock_key = f"release-locks/{tag}.lock"
        logger.debug(f"Releasing lock for tag: {tag}")

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            # check if we own the lock
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=lock_key)
            lock_data = json.loads(response["Body"].read().decode("utf-8"))

            if lock_data.get("owner") != self.owner:
                logger.error(f"Cannot release lock owned by: {lock_data.get('owner')}")
                return False

            self.s3_client.delete_object(Bucket=self.bucket_name, Key=lock_key)
            logger.debug("Lock released successfully")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.debug(f"No lock found for tag: {tag}")
                return True
            else:
                logger.error(f"Failed to release lock: {e}")
                raise


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
