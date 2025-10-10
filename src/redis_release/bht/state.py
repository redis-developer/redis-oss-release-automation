import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, Union

from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from rich.pretty import pretty_repr

from redis_release.models import WorkflowConclusion, WorkflowStatus
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


class Workflow(BaseModel):
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


class PackageMeta(BaseModel):
    """Metadata for a package."""

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


class ReleaseMeta(BaseModel):
    """Metadata for the release."""

    tag: Optional[str] = None


class ReleaseState(BaseModel):
    """Release state adapted for behavior tree usage."""

    meta: ReleaseMeta = Field(default_factory=ReleaseMeta)
    packages: Dict[str, Package] = Field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Config) -> "ReleaseState":
        """Build ReleaseState from config with default values."""
        packages = {}
        for package_name, package_config in config.packages.items():
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
                publish_internal_release=package_config.publish_internal_release,
            )

            # Initialize build workflow
            build_workflow = Workflow(
                workflow_file=package_config.build_workflow,
                inputs=package_config.build_inputs.copy(),
                timeout_minutes=package_config.build_timeout_minutes,
            )

            # Initialize publish workflow
            publish_workflow = Workflow(
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
        logger.info(f"Loading blackboard for tag: {tag}")

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=state_key)
            state_data: dict = json.loads(response["Body"].read().decode("utf-8"))

            logger.info("Blackboard loaded successfully")

            return state_data

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.info(f"No existing blackboard found for tag: {tag}")
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
        logger.info(f"Saving blackboard for tag: {tag}")

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

            logger.info("Blackboard saved successfully")

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
        logger.info(f"Acquiring lock for tag: {tag}")

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

            logger.info("Lock acquired successfully")
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
        logger.info(f"Releasing lock for tag: {tag}")

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
            logger.info("Lock released successfully")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.info(f"No lock found for tag: {tag}")
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
