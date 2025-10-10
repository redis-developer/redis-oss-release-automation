"""Data models for Redis release automation."""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ReleaseType(str, Enum):
    """Release type enumeration."""

    AUTO = "auto"
    PUBLIC = "public"
    PRIVATE = "private"


class WorkflowStatus(str, Enum):
    """Workflow status enumeration."""

    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class WorkflowConclusion(str, Enum):
    """Workflow conclusion enumeration."""

    SUCCESS = "success"
    FAILURE = "failure"


class PackageType(str, Enum):
    """Package type enumeration."""

    DOCKER = "docker"


class WorkflowRun(BaseModel):
    """Represents a GitHub workflow run."""

    repo: str
    workflow_id: str
    workflow_uuid: Optional[str] = None
    run_id: Optional[int] = None
    status: Optional[WorkflowStatus] = None
    conclusion: Optional[WorkflowConclusion] = None


class PackageState(BaseModel):
    """State of a package in the release process."""

    package_type: PackageType
    build_workflow: Optional[WorkflowRun] = None
    build_artifacts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    release_handle: Optional[Dict[str, Any]] = None
    build_completed: bool = False

    # Publish phase information
    publish_workflow: Optional[WorkflowRun] = None
    publish_completed: bool = False
    publish_info: Optional[Dict[str, Any]] = None
    publish_artifacts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    def is_build_phase_successful(self) -> bool:
        """Check if build workflow is completed successfully."""
        return (
            self.build_completed
            and self.build_workflow is not None
            and self.build_workflow.conclusion == WorkflowConclusion.SUCCESS
        )

    def is_publish_phase_successful(self) -> bool:
        """Check if publish workflow is completed successfully."""
        return (
            self.publish_completed
            and self.publish_workflow is not None
            and self.publish_workflow.conclusion == WorkflowConclusion.SUCCESS
        )


class ReleaseState(BaseModel):
    """Complete state of a release process."""

    tag: str
    release_type: ReleaseType
    packages: Dict[PackageType, PackageState] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    # Git commit information
    redis_tag_commit: Optional[str] = None  # Redis tag commit hash
    docker_repo_commit: Optional[str] = None  # Docker repo latest commit hash

    def is_build_successful(self) -> bool:
        """Check if all build workflows are completed successfully."""
        if not self.packages:
            return False
        return all(pkg.is_build_phase_successful() for pkg in self.packages.values())

    def is_build_phase_finished(self) -> bool:
        """Check if all build workflows are finished (successfully or not)."""
        if not self.packages:
            return False
        return all(pkg.build_completed for pkg in self.packages.values())

    def has_build_failures(self) -> bool:
        """Check if any build workflows failed or were cancelled."""
        if not self.packages:
            return False
        return any(
            pkg.build_completed
            and pkg.build_workflow
            and pkg.build_workflow.conclusion != WorkflowConclusion.SUCCESS
            for pkg in self.packages.values()
        )

    def is_publish_successful(self) -> bool:
        """Check if all publish workflows are completed successfully."""
        if not self.packages:
            return False
        return all(pkg.is_publish_phase_successful() for pkg in self.packages.values())

    def is_publish_phase_finished(self) -> bool:
        """Check if all publish workflows are finished (successfully or not)."""
        if not self.packages:
            return False
        return all(pkg.publish_completed for pkg in self.packages.values())

    def has_publish_failures(self) -> bool:
        """Check if any publish workflows failed or were cancelled."""
        if not self.packages:
            return False
        return any(
            pkg.publish_completed
            and pkg.publish_workflow
            and pkg.publish_workflow.conclusion != WorkflowConclusion.SUCCESS
            for pkg in self.packages.values()
        )


class RedisVersion(BaseModel):
    """Represents a parsed Redis version.

    TODO: This class duplicates the code from docker-library-redis/redis-release
    """

    major: int = Field(..., ge=1, description="Major version number")
    minor: int = Field(..., ge=0, description="Minor version number")
    patch: Optional[int] = Field(None, ge=0, description="Patch version number")
    suffix: str = Field("", description="Version suffix (e.g., -m01, -rc1, -eol)")

    @classmethod
    def parse(cls, version_str: str) -> "RedisVersion":
        """Parse a version string into components.

        Args:
            version_str: Version string (e.g., "v8.2.1-m01", "8.2", "7.4.0-eol")

        Returns:
            RedisVersion instance

        Raises:
            ValueError: If version string format is invalid
        """
        # Remove 'v' prefix if present
        version = version_str.lstrip("v")

        # Extract numeric part and suffix
        match = re.match(r"^([1-9]\d*\.\d+(?:\.\d+)?)(.*)", version)
        if not match:
            raise ValueError(f"Invalid version format: {version_str}")

        numeric_part, suffix = match.groups()

        # Parse numeric components
        parts = numeric_part.split(".")
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else None

        return cls(major=major, minor=minor, patch=patch, suffix=suffix)

    @property
    def is_milestone(self) -> bool:
        """Check if this is a milestone version (has suffix)."""
        return bool(self.suffix)

    @property
    def is_eol(self) -> bool:
        """Check if this version is end-of-life."""
        return self.suffix.lower().endswith("-eol")

    @property
    def mainline_version(self) -> str:
        """Get the mainline version string (major.minor)."""
        return f"{self.major}.{self.minor}"

    @property
    def sort_key(self) -> str:
        suffix_weight = 0
        if self.suffix.startswith("rc"):
            suffix_weight = 100
        elif self.suffix.startswith("m"):
            suffix_weight = 50

        return (
            f"{self.major}.{self.minor}.{self.patch or 0}.{suffix_weight}.{self.suffix}"
        )

    def __str__(self) -> str:
        """String representation of the version."""
        version = f"{self.major}.{self.minor}"
        if self.patch is not None:
            version += f".{self.patch}"
        return version + self.suffix

    def __lt__(self, other: "RedisVersion") -> bool:
        """Compare versions for sorting."""
        if not isinstance(other, RedisVersion):
            return NotImplemented

        # Compare major.minor.patch first
        self_tuple = (self.major, self.minor, self.patch or 0)
        other_tuple = (other.major, other.minor, other.patch or 0)

        if self_tuple != other_tuple:
            return self_tuple < other_tuple

        # If numeric parts are equal, compare suffixes
        # Empty suffix (GA) comes after suffixes (milestones)
        if not self.suffix and other.suffix:
            return False
        if self.suffix and not other.suffix:
            return True

        return self.suffix < other.suffix
