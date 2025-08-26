"""Data models for Redis release automation."""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

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
    run_id: Optional[int] = None
    status: Optional[WorkflowStatus] = None
    conclusion: Optional[WorkflowConclusion] = None


class PackageState(BaseModel):
    """State of a package in the release process."""

    package_type: PackageType
    build_workflow: Optional[WorkflowRun] = None
    artifact_urls: List[str] = Field(default_factory=list)
    build_completed: bool = False


class ReleaseState(BaseModel):
    """Complete state of a release process."""

    tag: str
    release_type: ReleaseType
    force_rebuild: bool = False
    packages: Dict[PackageType, PackageState] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    # Git commit information
    redis_tag_commit: Optional[str] = None  # Redis tag commit hash
    docker_repo_commit: Optional[str] = None  # Docker repo latest commit hash

    def is_build_phase_complete(self) -> bool:
        """Check if all build workflows are completed successfully."""
        if not self.packages:
            return False
        return all(
            pkg.build_completed
            and pkg.build_workflow
            and pkg.build_workflow.conclusion == WorkflowConclusion.SUCCESS
            for pkg in self.packages.values()
        )

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
