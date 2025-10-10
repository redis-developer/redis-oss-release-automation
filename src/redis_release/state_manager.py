"""State management for Redis release automation."""

import json
import logging
import os
from builtins import NotImplementedError
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from .models import ReleaseState

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

        # local state cache for dry run mode
        self._local_state_cache = {}

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
                    logger.error("AWS credentials not found")
                    logger.warning(
                        "Set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY environment variables"
                    )
                    raise NoCredentialsError()

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
                raise
            except Exception as e:
                logger.error(f"AWS authentication error: {e}")
                raise

        return self._s3_client


class StateManager(S3Backed):
    """Manages release state persistence in S3."""

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        dry_run: bool = False,
        aws_region: str = "us-east-1",
        aws_profile: Optional[str] = None,
    ):
        super().__init__(bucket_name, dry_run, aws_region, aws_profile)

    def _create_bucket(self) -> None:
        """Create S3 bucket if it doesn't exist."""
        if self._s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            logger.info(f"Creating S3 bucket: {self.bucket_name}")

            if self.aws_region == "us-east-1":
                self._s3_client.create_bucket(Bucket=self.bucket_name)
            else:
                self._s3_client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.aws_region},
                )

            self._s3_client.put_bucket_versioning(
                Bucket=self.bucket_name, VersioningConfiguration={"Status": "Enabled"}
            )

            logger.info(f"S3 bucket created successfully: {self.bucket_name}")

        except ClientError as e:
            if e.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
                logger.warning(f"Bucket already exists: {self.bucket_name}")
            else:
                logger.error(f"Failed to create bucket: {e}")
                raise

    def load_state(self, tag: str) -> Optional[ReleaseState]:
        """Load release state from S3.

        Args:
            tag: Release tag

        Returns:
            ReleaseState object or None if not found
        """
        state_key = f"release-state/{tag}.json"
        logger.info(f"Loading state for tag: {tag}")

        if self.dry_run:
            state_data = self._local_state_cache.get(state_key)
            if state_data:
                logger.debug("DRY RUN - loaded from local cache")
                return ReleaseState.model_validate(state_data)
            else:
                logger.debug("DRY RUN - no state found in cache")
                return None

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=state_key)
            state_data = json.loads(response["Body"].read().decode("utf-8"))

            logger.info("State loaded successfully")

            return ReleaseState.model_validate(state_data)

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning(f"No existing state found for tag: {tag}")
                return None
            else:
                logger.error(f"Failed to load state: {e}")
                raise

    def save_state(self, state: ReleaseState) -> None:
        """Save release state to S3.

        Args:
            state: ReleaseState object to save
        """
        state_key = f"release-state/{state.tag}.json"
        logger.info(f"Saving state for tag: {state.tag}")

        state_data = state.model_dump(mode="json")
        state_json = json.dumps(state_data, indent=2, default=str)

        if self.dry_run:
            logger.debug("DRY RUN - saved to local cache")
            self._local_state_cache[state_key] = state_data
            return

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=state_key,
                Body=state_json,
                ContentType="application/json",
                Metadata={
                    "tag": state.tag,
                    "release_type": state.release_type.value,
                },
            )

            logger.info("State saved successfully")

        except ClientError as e:
            logger.error(f"Failed to save state: {e}")
            raise

    def acquire_lock(self, tag: str, owner: str) -> bool:
        """Acquire a lock for the release process.

        Args:
            tag: Release tag
            owner: Lock owner identifier

        Returns:
            True if lock acquired successfully
        """
        lock_key = f"release-locks/{tag}.lock"
        logger.info(f"Acquiring lock for tag: {tag}")

        if self.dry_run:
            logger.debug("DRY RUN - lock acquired")
            return True

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        lock_data = {
            "tag": tag,
            "owner": owner,
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
                    logger.error(
                        f"Lock already held by: {existing_lock.get('owner', 'unknown')}"
                    )
                    logger.debug(
                        f"Acquired at: {existing_lock.get('acquired_at', 'unknown')}"
                    )
                except:
                    logger.error("Lock exists but couldn't read details")
                return False
            else:
                logger.error(f"Failed to acquire lock: {e}")
                raise

    def release_lock(self, tag: str, owner: str) -> bool:
        """Release a lock for the release process.

        Args:
            tag: Release tag
            owner: Lock owner identifier

        Returns:
            True if lock released successfully
        """
        lock_key = f"release-locks/{tag}.lock"
        logger.info(f"Releasing lock for tag: {tag}")

        if self.dry_run:
            logger.debug("DRY RUN - lock released")
            return True

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            # check if we own the lock
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=lock_key)
            lock_data = json.loads(response["Body"].read().decode("utf-8"))

            if lock_data.get("owner") != owner:
                logger.error(f"Cannot release lock owned by: {lock_data.get('owner')}")
                return False

            self.s3_client.delete_object(Bucket=self.bucket_name, Key=lock_key)
            logger.info("Lock released successfully")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning(f"No lock found for tag: {tag}")
                return True
            else:
                logger.error(f"Failed to release lock: {e}")
                raise
