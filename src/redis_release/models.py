"""Data models for Redis release automation."""

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from redis_version import RedisVersion


class WorkflowType(str, Enum):
    """Workflow type enumeration."""

    BUILD = "build"
    PUBLISH = "publish"


class PackageType(str, Enum):
    """Package type enumeration."""

    DOCKER = "docker"
    DEBIAN = "debian"
    RPM = "rpm"
    CLI_STATIC = "cli-static"
    HOMEBREW = "homebrew"
    SNAP = "snap"
    CLIENTIMAGE = "clientimage"
    CLIENTTEST = "clienttest"


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
    COMPACT = "compact"


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
    TIMESERIES = "redistimeseries"
    BLOOM = "redisbloom"


class WorkflowRun(BaseModel):
    """Represents a GitHub workflow run."""

    repo: str
    workflow_id: str
    workflow_uuid: Optional[str] = None
    run_id: Optional[int] = None
    status: Optional[WorkflowStatus] = None
    conclusion: Optional[WorkflowConclusion] = None


class ReleaseArgs(BaseModel):
    """Arguments for release execution."""

    release_tag: str
    force_rebuild: List[str] = Field(default_factory=list)
    only_packages: List[str] = Field(default_factory=list)
    force_release_type: Dict[str, ReleaseType] = Field(default_factory=dict)
    override_state_name: Optional[str] = None
    module_versions: Dict[RedisModule, str] = Field(default_factory=dict)

    slack_args: Optional["SlackArgs"] = None
    custom_build: bool = False
    # nightly_build enforces nightly build configuration: each module is master
    # and run_type is set to nightly. custom_build is implied.
    nightly_build: bool = False

    @model_validator(mode="after")
    def _nightly_implies_custom(self) -> "ReleaseArgs":
        if self.nightly_build:
            self.custom_build = True
            self.module_versions = {module: "master" for module in RedisModule}
        return self


class SlackArgs(BaseModel):
    bot_token: Optional[str] = None
    channel_id: Optional[str] = None
    thread_ts: Optional[str] = None
    reply_broadcast: bool = False
    format: SlackFormat = SlackFormat.DEFAULT
