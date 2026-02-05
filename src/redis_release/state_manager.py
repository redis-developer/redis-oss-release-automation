"""State management for Redis release automation."""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Protocol

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from rich.pretty import pretty_repr

from redis_release.bht.state import ReleaseState, logger
from redis_release.config import Config

from .bht.state import DockerMeta, PackageType, ReleaseState
from .logging_config import log_once
from .models import ReleaseArgs

logger = logging.getLogger(__name__)


class S3Backed:
    def __init__(
        self,
        bucket_name: Optional[str] = None,
        dry_run: bool = False,
        aws_region: str = "us-east-1",
        aws_profile: Optional[str] = None,
    ):
        """Initialize state manager.

        Args:
            bucket_name: S3 bucket name for state storage
            dry_run: If True, simulate operations without making real S3 calls
            aws_region: AWS region for S3 bucket
            aws_profile: AWS profile name to use for authentication
        """
        self.bucket_name = bucket_name or os.getenv(
            "REDIS_RELEASE_STATE_BUCKET", "redis-release-state"
        )
        self.dry_run = dry_run
        self.aws_region = aws_region
        self.aws_profile = aws_profile or os.getenv("AWS_PROFILE")
        self._s3_client = None

        # AWS credentials from environment variables only
        self.aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        self.aws_session_token = os.getenv("AWS_SESSION_TOKEN")

    @property
    def s3_client(self) -> Optional[boto3.client]:
        """Lazy initialization of S3 client."""
        if self._s3_client is None and not self.dry_run:
            try:
                # Try profile-based authentication first
                if self.aws_profile:
                    logger.info(f"Using AWS profile: {self.aws_profile}")
                    session = boto3.Session(profile_name=self.aws_profile)
                    self._s3_client = session.client("s3", region_name=self.aws_region)
                # Fall back to environment variables
                elif self.aws_access_key_id and self.aws_secret_access_key:
                    logger.info("Using AWS credentials from environment variables")
                    self._s3_client = boto3.client(
                        "s3",
                        aws_access_key_id=self.aws_access_key_id,
                        aws_secret_access_key=self.aws_secret_access_key,
                        aws_session_token=self.aws_session_token,
                        region_name=self.aws_region,
                    )
                else:
                    # Fall back to default credential chain (includes EC2 instance profile, ECS task role, etc.)
                    logger.info(
                        "Using AWS default credential chain (EC2 instance profile, ECS task role, etc.)"
                    )
                    self._s3_client = boto3.client("s3", region_name=self.aws_region)

                # Test connection
                self._s3_client.head_bucket(Bucket=self.bucket_name)
                logger.info(f"Connected to S3 bucket: {self.bucket_name}")

            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    logger.warning(f"S3 bucket not found: {self.bucket_name}")
                    self._create_bucket()
                else:
                    logger.error(f"S3 error: {e}")
                    raise
            except NoCredentialsError:
                logger.error("AWS credentials not found")
                logger.warning(
                    "Set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY environment variables, "
                    "or run on EC2 instance with IAM role"
                )
                raise
            except Exception as e:
                logger.error(f"AWS authentication error: {e}")
                raise

        return self._s3_client


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


class StateManager:
    """Syncs ReleaseState to storage backend only when changed.

    Can be used as a context manager to automatically acquire and release locks.
    """

    def __init__(
        self,
        storage: StateStorage,
        config: Config,
        args: "ReleaseArgs",
        read_only: bool = False,
    ):
        self.tag = args.release_tag
        self.storage = storage
        self.config = config
        self.args = args
        self.last_dump: Optional[str] = None
        self._state: Optional[ReleaseState] = None
        self._lock_acquired = False
        self.read_only = read_only

        # allow custom name for state, to be able to make test runs with real tags
        # without affecting production state
        if args.override_state_name:
            logger.info(
                f"Using custom state name instead of release tag: {args.override_state_name}"
            )
            self.state_name = args.override_state_name
        else:
            self.state_name = self.tag

    def __enter__(self) -> "StateManager":
        if self.read_only:
            return self
        """Acquire lock when entering context."""
        if not self.storage.acquire_lock(self.state_name):
            raise RuntimeError(f"Failed to acquire lock for tag: {self.state_name}")
        self._lock_acquired = True
        logger.info(f"Lock acquired for tag: {self.state_name}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.read_only:
            return
        """Release lock when exiting context."""
        if self._lock_acquired:
            self.storage.release_lock(self.state_name)
            self._lock_acquired = False
            logger.info(f"Lock released for tag: {self.state_name}")

    @property
    def state(self) -> ReleaseState:
        if self._state is None:
            loaded = None
            if self.args.force_rebuild and "all" in self.args.force_rebuild:
                logger.info(
                    "Force rebuild 'all' enabled, using default state based on config"
                )
                loaded = self.default_state()
            else:
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
        self.apply_args(state, quiet=True)
        return state

    def apply_args(self, state: ReleaseState, quiet: bool = False) -> None:
        """Apply arguments to state."""
        # Prefer state tag if present
        if not state.meta.tag:
            state.meta.tag = self.tag
        else:
            self.tag = state.meta.tag

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

            if self.args.force_release_type:
                if not quiet:
                    logger.info(f"Force release type: {self.args.force_release_type}")
                # Handle "all" keyword to apply to all packages
                if "all" in self.args.force_release_type:
                    release_type = self.args.force_release_type["all"]
                    for package_name in state.packages:
                        state.packages[package_name].meta.release_type = release_type
                        logger.info(
                            f"Set release type for package '{package_name}': {release_type}"
                        )
                else:
                    # Set release type for specific packages
                    for (
                        package_name,
                        release_type,
                    ) in self.args.force_release_type.items():
                        if package_name in state.packages:
                            state.packages[package_name].meta.release_type = (
                                release_type
                            )
                            logger.info(
                                f"Set release type for package '{package_name}': {release_type}"
                            )
                        else:
                            logger.warning(
                                f"Package '{package_name}' not found in state, skipping release type override"
                            )
            if self.args.module_versions:
                for package in state.packages.values():
                    if package.meta.package_type == PackageType.DOCKER:
                        assert isinstance(package.meta, DockerMeta)
                        if not quiet:
                            for module, version in self.args.module_versions.items():
                                logger.info(
                                    f"Set module version for [yellow]{module}: {version}[/]"
                                )
                        package.meta.module_versions = self.args.module_versions

            if self.args.slack_args:
                state.meta.ephemeral.slack_channel_id = self.args.slack_args.channel_id
                state.meta.ephemeral.slack_thread_ts = self.args.slack_args.thread_ts

            if self.args.custom_build:
                state.meta.is_custom_build = True

    def load(self) -> Optional[ReleaseState]:
        """Load state from storage backend."""
        state_data = self.storage.get(self.state_name)
        if state_data is None:
            return None

        state = ReleaseState.from_json(state_data)

        # Reset ephemeral fields to defaults if not in read-only mode
        if not self.read_only:
            self._reset_ephemeral_fields(state)

        self.last_dump = state.model_dump_json(indent=2)
        return state

    def _reset_ephemeral_fields(self, state: ReleaseState) -> None:
        """Reset ephemeral fields to defaults (except log_once_flags which are always reset)."""

        # Reset release meta ephemeral
        state.meta.ephemeral = state.meta.ephemeral.__class__()

        # Reset package ephemeral fields
        for package in state.packages.values():
            package.meta.ephemeral = package.meta.ephemeral.__class__()  # type: ignore
            package.build.ephemeral = package.build.ephemeral.__class__()
            if package.publish is not None:
                package.publish.ephemeral = package.publish.ephemeral.__class__()

    def sync(self) -> None:
        """Save state to storage backend if changed since last sync."""
        if self.read_only:
            raise RuntimeError("Cannot sync read-only state")
        current_dump = self.state.model_dump_json(indent=2)

        if current_dump != self.last_dump:
            self.last_dump = current_dump
            state_dict = json.loads(current_dump)
            self.storage.put(self.state_name, state_dict)
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
