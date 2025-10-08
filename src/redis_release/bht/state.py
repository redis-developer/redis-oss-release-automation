import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from pydantic import BaseModel, Field
from rich.pretty import pretty_repr

from redis_release.models import WorkflowConclusion, WorkflowStatus

from ..config import Config

if TYPE_CHECKING:
    from .args import ReleaseArgs

logger = logging.getLogger(__name__)


class WorkflowEphemeral(BaseModel):
    """Ephemeral workflow state that is not persisted."""

    trigger_failed: bool = False
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


class StateSyncer:
    """Syncs ReleaseState to file only when changed."""

    def __init__(
        self,
        config: Config,
        args: Optional["ReleaseArgs"] = None,
        file_path: Union[str, Path] = "state.json",
    ):
        self.config = config
        self.args = args
        self.file_path = Path(file_path)
        self.last_dump: Optional[str] = None
        self._state: Optional[ReleaseState] = None

    @property
    def state(self) -> ReleaseState:
        if self._state is None:
            loaded = self.load()
            if loaded is None:
                self._state = ReleaseState.from_config(self.config)
                # Set tag from args when creating from config
                if self.args:
                    self._state.meta.tag = self.args.release_tag
            else:
                self._state = loaded

            # Apply force_rebuild flags from args
            if self.args:
                if "all" in self.args.force_rebuild:
                    # Set force_rebuild for all packages
                    for package_name in self._state.packages:
                        self._state.packages[
                            package_name
                        ].meta.ephemeral.force_rebuild = True
                else:
                    # Set force_rebuild for specific packages
                    for package_name in self.args.force_rebuild:
                        if package_name in self._state.packages:
                            self._state.packages[
                                package_name
                            ].meta.ephemeral.force_rebuild = True
            logger.debug(pretty_repr(self._state))
        return self._state

    def load(self) -> Optional[ReleaseState]:
        if not self.file_path.exists():
            return None

        with open(self.file_path, "r") as f:
            json_data = json.load(f)

        state = ReleaseState(**json_data)
        self.last_dump = state.model_dump_json(indent=2)
        return state

    def sync(self) -> None:
        """Save state to file if changed since last sync."""
        current_dump = self.state.model_dump_json(indent=2)

        if current_dump != self.last_dump:
            self.last_dump = current_dump
            with open(self.file_path, "w") as f:
                f.write(current_dump)
            logger.debug("State saved")
