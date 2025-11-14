import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from py_trees import common
from py_trees.common import Status
from pydantic import BaseModel, Field

from redis_release.models import (
    PackageType,
    ReleaseType,
    WorkflowConclusion,
    WorkflowStatus,
    WorkflowType,
)

from ..config import Config

logger = logging.getLogger(__name__)

SUPPORTED_STATE_VERSION = 2


class WorkflowEphemeral(BaseModel):
    """Ephemeral workflow state. Reset on each run.

    The main purpose of ephemeral fields is to prevent retry loops and to allow extensive status reporting.

    Each workflow step has a pair of fields indicating the step status:
    One ephemeral field is set when the step is attempted. It may have four states:
    - `None` (default): Step has not been attempted
    - `common.Status.RUNNING`: Step is currently running
    - `common.Status.FAILURE`: Step has been attempted and failed
    - `common.Status.SUCCESS`: Step has been attempted and succeeded

    Ephemeral fields are reset on each run. Their values are persisted but only until
    next run is started.
    So they indicate either current (if run is in progress) or last run state.

    The other field indicates the step result, it may either have some value or be empty.

    For example for trigger step we have `trigger_workflow` ephemeral
    and `triggered_at` result fields.

    Optional message field may be used to provide additional information about the step.
    For example wait_for_completion_message may contain information about timeout.

    Given combination of ephemeral and result fields we can determine step status.
    Each step may be in one of the following states:
        Not started
        Failed
        Succeeded or OK
        Incorrect (this shouln't happen)

    The following decision table show how step status is determined for trigger step.
    In general this is applicable to all steps.

    tigger_workflow -> | None (default) |     Running    |   Failure   |  Success   |
    triggered_at:      |                |                |             |            |
       None            |   Not started  |   In progress  |    Failed   |  Incorrect |
      Has value        |       OK       |    Incorrect   |  Incorrect  |     OK     |

    The result field (triggered_at in this case) should not be set while step is
    running, if step was not started or if it's failed.
    And it should be set if trigger_workflow is successful.
    It may be set if trigger_workflow is None, which is the case when release
    process was restarted and all ephemeral fields are reset, but the particular
    step was successful in previous run.

    Correct values are not eforced it's up to the implementation to correctly
    set the fields.
    """

    identify_workflow: Optional[common.Status] = None
    trigger_workflow: Optional[common.Status] = None
    wait_for_completion: Optional[common.Status] = None
    wait_for_completion_message: Optional[str] = None
    download_artifacts: Optional[common.Status] = None
    extract_artifact_result: Optional[common.Status] = None

    log_once_flags: Dict[str, bool] = Field(default_factory=dict, exclude=True)


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
    ephemeral: WorkflowEphemeral = Field(default_factory=WorkflowEphemeral)


class PackageMetaEphemeral(BaseModel):
    """Ephemeral package metadata. Reset on each run.

    See WorkflowEphemeral for more details.
    """

    force_rebuild: bool = False
    identify_ref_failed: bool = False
    identify_ref: Optional[common.Status] = None
    log_once_flags: Dict[str, bool] = Field(default_factory=dict, exclude=True)


class PackageMeta(BaseModel):
    """Metadata for a package."""

    package_type: Optional[PackageType] = None
    repo: str = ""
    ref: Optional[str] = None
    publish_internal_release: bool = False
    ephemeral: PackageMetaEphemeral = Field(default_factory=PackageMetaEphemeral)


class Package(BaseModel):
    """State for a package in the release."""

    meta: PackageMeta = Field(default_factory=PackageMeta)
    build: Workflow = Field(default_factory=Workflow)
    publish: Workflow = Field(default_factory=Workflow)


class ReleaseMetaEphemeral(BaseModel):
    """Ephemeral release metadata. Reset on each run.

    See WorkflowEphemeral for more details.
    """

    log_once_flags: Dict[str, bool] = Field(default_factory=dict, exclude=True)


class ReleaseMeta(BaseModel):
    """Metadata for the release."""

    tag: Optional[str] = None
    release_type: Optional[ReleaseType] = None
    ephemeral: ReleaseMetaEphemeral = Field(default_factory=ReleaseMetaEphemeral)


class ReleaseState(BaseModel):
    """Release state adapted for behavior tree usage."""

    version: int = 2
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

        if json_data.get("version") != SUPPORTED_STATE_VERSION:
            raise ValueError(
                f"Unsupported state version: {json_data.get('version')}, "
                f"expected: {SUPPORTED_STATE_VERSION}"
            )
        return cls(**json_data)


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
