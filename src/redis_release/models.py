"""Data models for Redis release automation."""

import functools
import re
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowType(str, Enum):
    """Workflow type enumeration."""

    BUILD = "build"
    PUBLISH = "publish"


class PackageType(str, Enum):
    """Package type enumeration."""

    DOCKER = "docker"
    DEBIAN = "debian"
    RPM = "rpm"
    HOMEBREW = "homebrew"
    SNAP = "snap"
    CLIENTIMAGE = "clientimage"


class HomebrewChannel(str, Enum):
    """Homebrew channel enumeration."""

    STABLE = "stable"
    RC = "rc"


class SnapRiskLevel(str, Enum):
    """Snap channel enumeration."""

    STABLE = "stable"
    CANDIDATE = "candidate"
    BETA = "beta"
    EDGE = "edge"


class ReleaseType(str, Enum):
    """Release type enumeration."""

    PUBLIC = "public"
    INTERNAL = "internal"


class SlackFormat(str, Enum):
    """Slack message format enumeration."""

    DEFAULT = "default"
    ONE_STEP = "one-step"


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


class RedisModule(str, Enum):
    """Redis module enumeration."""

    JSON = "redisjson"
    SEARCH = "redisearch"
    TIMESERIES = "reduistimeseries"
    BLOOM = "redisbloom"


class WorkflowRun(BaseModel):
    """Represents a GitHub workflow run."""

    repo: str
    workflow_id: str
    workflow_uuid: Optional[str] = None
    run_id: Optional[int] = None
    status: Optional[WorkflowStatus] = None
    conclusion: Optional[WorkflowConclusion] = None


@functools.total_ordering
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
    def is_rc(self) -> bool:
        """Check if this version is a release candidate."""
        return self.suffix.lower().startswith("-rc")

    @property
    def is_ga(self) -> bool:
        """Check if this version is a general availability (GA) release."""
        return not self.is_milestone

    @property
    def is_internal(self) -> bool:
        """Check if this version is an internal release."""
        return bool(re.search(r"-int\d*$", self.suffix.lower()))

    @property
    def mainline_version(self) -> str:
        """Get the mainline version string (major.minor)."""
        return f"{self.major}.{self.minor}"

    @property
    def suffix_weight(self) -> str:
        # warning: using lexicographic order, letters doesn't have any meaning except for ordering
        suffix_weight = ""
        if self.is_ga:
            suffix_weight = "QQ"
        if self.is_rc:
            suffix_weight = "LL"
        elif self.suffix.startswith("-m"):
            suffix_weight = "II"

        # internal versions are always lower than their GA/rc/m counterparts
        if self.is_internal:
            suffix_weight = suffix_weight[:1] + "E"

        return suffix_weight

    @property
    def sort_key(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch or 0}.{self.suffix_weight}{self.suffix}"

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

        return self.sort_key < other.sort_key

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RedisVersion):
            return NotImplemented

        return self.sort_key == other.sort_key

    def __hash__(self) -> int:
        """Hash for use in sets and dicts."""
        return hash((self.major, self.minor, self.patch or 0, self.suffix))


class ReleaseArgs(BaseModel):
    """Arguments for release execution."""

    release_tag: str
    force_rebuild: List[str] = Field(default_factory=list)
    only_packages: List[str] = Field(default_factory=list)
    force_release_type: Dict[str, ReleaseType] = Field(default_factory=dict)
    override_state_name: Optional[str] = None
    module_versions: Dict[RedisModule, str] = Field(default_factory=dict)

    slack_args: Optional["SlackArgs"] = None


class SlackArgs(BaseModel):
    bot_token: Optional[str] = None
    channel_id: Optional[str] = None
    thread_ts: Optional[str] = None
    reply_broadcast: bool = False
    format: SlackFormat = SlackFormat.DEFAULT
