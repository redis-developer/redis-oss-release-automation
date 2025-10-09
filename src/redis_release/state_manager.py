"""State management for Redis release automation."""

import json
import logging
import os
from builtins import NotImplementedError
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from rich.console import Console

from .models import ReleaseState

console = Console()
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
                    console.print(f"[blue]Using AWS profile: {self.aws_profile}[/blue]")
                    session = boto3.Session(profile_name=self.aws_profile)
                    self._s3_client = session.client("s3", region_name=self.aws_region)
                # Fall back to environment variables
                elif self.aws_access_key_id and self.aws_secret_access_key:
                    console.print(
                        "[blue]Using AWS credentials from environment variables[/blue]"
                    )
                    self._s3_client = boto3.client(
                        "s3",
                        aws_access_key_id=self.aws_access_key_id,
                        aws_secret_access_key=self.aws_secret_access_key,
                        aws_session_token=self.aws_session_token,
                        region_name=self.aws_region,
                    )
                else:
                    console.print("[red]AWS credentials not found[/red]")
                    console.print(
                        "[yellow]Set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY environment variables[/yellow]"
                    )
                    raise NoCredentialsError()

                # Test connection
                self._s3_client.head_bucket(Bucket=self.bucket_name)
                console.print(
                    f"[green]Connected to S3 bucket: {self.bucket_name}[/green]"
                )

            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    console.print(
                        f"[yellow]S3 bucket not found: {self.bucket_name}[/yellow]"
                    )
                    self._create_bucket()
                else:
                    console.print(f"[red]S3 error: {e}[/red]")
                    raise
            except NoCredentialsError:
                raise
            except Exception as e:
                console.print(f"[red]AWS authentication error: {e}[/red]")
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
            console.print(f"[blue] Creating S3 bucket: {self.bucket_name}[/blue]")

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

            console.print(
                f"[green] S3 bucket created successfully: {self.bucket_name}[/green]"
            )

        except ClientError as e:
            if e.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
                console.print(
                    f"[yellow] Bucket already exists: {self.bucket_name}[/yellow]"
                )
            else:
                console.print(f"[red] Failed to create bucket: {e}[/red]")
                raise

    def load_state(self, tag: str) -> Optional[ReleaseState]:
        """Load release state from S3.

        Args:
            tag: Release tag

        Returns:
            ReleaseState object or None if not found
        """
        state_key = f"release-state/{tag}.json"
        console.print(f"[blue] Loading state for tag: {tag}[/blue]")

        if self.dry_run:
            state_data = self._local_state_cache.get(state_key)
            if state_data:
                console.print("[yellow]   (DRY RUN - loaded from local cache)[/yellow]")
                return ReleaseState.model_validate(state_data)
            else:
                console.print("[yellow]   (DRY RUN - no state found in cache)[/yellow]")
                return None

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=state_key)
            state_data = json.loads(response["Body"].read().decode("utf-8"))

            console.print(f"[green]State loaded successfully[/green]")

            return ReleaseState.model_validate(state_data)

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                console.print(
                    f"[yellow] No existing state found for tag: {tag}[/yellow]"
                )
                return None
            else:
                console.print(f"[red] Failed to load state: {e}[/red]")
                raise

    def save_state(self, state: ReleaseState) -> None:
        """Save release state to S3.

        Args:
            state: ReleaseState object to save
        """
        state_key = f"release-state/{state.tag}.json"
        console.print(f"[blue] Saving state for tag: {state.tag}[/blue]")

        state_data = state.model_dump(mode="json")
        state_json = json.dumps(state_data, indent=2, default=str)

        if self.dry_run:
            console.print("[yellow]   (DRY RUN - saved to local cache)[/yellow]")
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

            console.print(f"[green] State saved successfully[/green]")

        except ClientError as e:
            console.print(f"[red] Failed to save state: {e}[/red]")
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
        console.print(f"[blue] Acquiring lock for tag: {tag}[/blue]")

        if self.dry_run:
            console.print("[yellow] (DRY RUN - lock acquired)[/yellow]")
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

            console.print(f"[green] Lock acquired successfully[/green]")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "PreconditionFailed":
                try:
                    response = self.s3_client.get_object(
                        Bucket=self.bucket_name, Key=lock_key
                    )
                    existing_lock = json.loads(response["Body"].read().decode("utf-8"))
                    console.print(
                        f"[red]   Lock already held by: {existing_lock.get('owner', 'unknown')}[/red]"
                    )
                    console.print(
                        f"[dim]   Acquired at: {existing_lock.get('acquired_at', 'unknown')}[/dim]"
                    )
                except:
                    console.print(f"[red] Lock exists but couldn't read details[/red]")
                return False
            else:
                console.print(f"[red] Failed to acquire lock: {e}[/red]")
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
        console.print(f"[blue] Releasing lock for tag: {tag}[/blue]")

        if self.dry_run:
            console.print("[yellow]   (DRY RUN - lock released)[/yellow]")
            return True

        if self.s3_client is None:
            raise RuntimeError("S3 client not initialized")

        try:
            # check if we own the lock
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=lock_key)
            lock_data = json.loads(response["Body"].read().decode("utf-8"))

            if lock_data.get("owner") != owner:
                console.print(
                    f"[red] Cannot release lock owned by: {lock_data.get('owner')}[/red]"
                )
                return False

            self.s3_client.delete_object(Bucket=self.bucket_name, Key=lock_key)
            console.print(f"[green] Lock released successfully[/green]")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                console.print(f"[yellow]  No lock found for tag: {tag}[/yellow]")
                return True
            else:
                console.print(f"[red] Failed to release lock: {e}[/red]")
                raise
