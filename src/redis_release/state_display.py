"""Console display utilities for release state.

Models help to organize basic display logic and structure and make it reusable across different output formats.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

from py_trees import common
from py_trees.common import Status

from redis_release.models import RedisModule, WorkflowConclusion, WorkflowType

from .bht.state import (
    ClientImageMeta,
    DockerMeta,
    ClientTestMeta,
    HomebrewMeta,
    Package,
    PackageMeta,
    ReleaseState,
    SnapMeta,
    Workflow,
)
from .models import PackageType


# See WorkflowEphemeral for more details on the flags and steps
class StepStatus(str, Enum):
    """Status of a workflow step."""

    NOT_STARTED = "not_started"
    RUNNING = "in_progress"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    INCORRECT = "incorrect"


@dataclass
class Step:
    status: StepStatus = StepStatus.INCORRECT
    name: str = ""
    message: Optional[str] = None
    has_result: bool = False
    ephemeral_status: Optional[Status] = None


@dataclass
class Section:
    name: str
    is_workflow: bool = False


# Decision table for step status
# See state.py for detailed description of how the flags are used to determine
# the step status
_STEP_STATUS_MAPPING = {
    None: {False: StepStatus.NOT_STARTED, True: StepStatus.SUCCEEDED},
    Status.RUNNING: {False: StepStatus.RUNNING},
    Status.FAILURE: {False: StepStatus.FAILED},
    Status.SUCCESS: {True: StepStatus.SUCCEEDED},
}


class DisplayModelGeneric:
    """Model for computing display status from workflow state."""

    def get_custom_versions(self, state: ReleaseState) -> Dict[str, str]:
        """Get custom version information for display.

        Detects whether the build is custom using is_custom_build flag
        or if any module has a requested custom version.

        Args:
            state: The release state

        Returns:
            Dictionary of component names to versions. Empty if not a custom build.
            Contains 'redis: <release_tag>' if custom build, plus any explicit module versions.
        """
        result: Dict[str, str] = {}

        # Check if any docker package has explicit module versions
        docker_package = state.packages.get("docker")
        module_versions: Dict[RedisModule, str] = {}
        if docker_package and isinstance(docker_package.meta, DockerMeta):
            if docker_package.meta.module_versions:
                module_versions = docker_package.meta.module_versions

        # Determine if this is a custom build
        is_custom = state.meta.is_custom_build or bool(module_versions)

        if not is_custom:
            return result

        # Add redis version (release tag)
        if state.meta.tag:
            result["redis"] = state.meta.tag

        # Add explicit module versions
        for module, version in module_versions.items():
            result[module.value] = version

        return result

    def get_workflow_section(self, workflow: Workflow) -> Section:
        """Get the section name for a workflow based on its type.

        Args:
            workflow: The workflow to get the section for

        Returns:
            Section with the appropriate name
        """
        if workflow.workflow_type == WorkflowType.BUILD:
            return Section(name="Build Workflow", is_workflow=True)
        elif workflow.workflow_type == WorkflowType.PUBLISH:
            return Section(name="Publish Workflow", is_workflow=True)
        else:
            return Section(name="Workflow")

    def get_step_status(
        self, step_result: bool, step_status_flag: Optional[common.Status]
    ) -> StepStatus:
        """Get step status based on result and ephemeral flag.

        See WorkflowEphemeral for more details on the flags.

        Args:
            step_result: Whether the step has a result
            step_status_flag: The ephemeral status flag value

        Returns:
            The determined step status
        """
        if step_status_flag in _STEP_STATUS_MAPPING:
            if step_result in _STEP_STATUS_MAPPING[step_status_flag]:
                return _STEP_STATUS_MAPPING[step_status_flag][step_result]
        return StepStatus.INCORRECT

    def get_workflow_steps(
        self, package: Package, workflow: Workflow
    ) -> List[Union[Step, Section]]:
        """Get the list of workflow steps.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            List of Step and Section objects for the workflow, starting with the workflow section
        """
        return [
            self.get_workflow_section(workflow),
            Step(
                name="Identify target ref",
                has_result=package.meta.ref is not None,
                ephemeral_status=package.meta.ephemeral.identify_ref,
            ),
            Step(
                name="Trigger workflow",
                has_result=workflow.triggered_at is not None,
                ephemeral_status=workflow.ephemeral.trigger_workflow,
            ),
            Step(
                name="Find workflow run",
                has_result=workflow.run_id is not None,
                ephemeral_status=workflow.ephemeral.identify_workflow,
            ),
            Step(
                name="Wait for completion",
                has_result=workflow.conclusion == WorkflowConclusion.SUCCESS,
                ephemeral_status=workflow.ephemeral.wait_for_completion,
                message=workflow.ephemeral.wait_for_completion_message,
            ),
            Step(
                name="Download artifacts",
                has_result=workflow.artifacts is not None,
                ephemeral_status=workflow.ephemeral.download_artifacts,
            ),
            Step(
                name="Get result",
                has_result=workflow.result is not None,
                ephemeral_status=workflow.ephemeral.extract_artifact_result,
            ),
        ]

    def get_workflow_status(
        self, package: Package, workflow: Workflow
    ) -> Tuple[StepStatus, List[Union[Step, Section]]]:
        """Get workflow status based on ephemeral and result fields.

        Returns tuple of overall status and list of steps, with the workflow section as the first item.

        See WorkflowEphemeral for more details on the flags.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            Tuple of (overall_status, list starting with Section followed by Step objects)
        """
        steps = self.get_workflow_steps(package, workflow)
        steps_status: List[Union[Step, Section]] = []

        for item in steps:
            if isinstance(item, Section):
                # Sections are just added as-is
                steps_status.append(item)
            else:
                # Steps need their status computed
                item.status = self.get_step_status(
                    item.has_result, item.ephemeral_status
                )
                steps_status.append(item)
                if item.status != StepStatus.SUCCEEDED:
                    return (item.status, steps_status)
        return (StepStatus.SUCCEEDED, steps_status)


class DisplayModelWithReleaseValidation(DisplayModelGeneric):
    """DisplayModel for packages that require release validation (Homebrew, Snap)."""

    def get_workflow_steps(
        self, package: Package, workflow: Workflow
    ) -> List[Union[Step, Section]]:
        """Get the list of workflow steps with release validation prepended.

        Args:
            package: The package containing the workflow
            workflow: The workflow to check

        Returns:
            List of Step and Section objects for the workflow, with release validation section and step prepended
        """
        assert isinstance(package.meta, (HomebrewMeta, SnapMeta)), (
            f"DisplayModelWithReleaseValidation requires HomebrewMeta or SnapMeta, "
            f"got {type(package.meta).__name__}"
        )

        result: List[Union[Step, Section]] = []
        base_steps = super().get_workflow_steps(package, workflow)

        if workflow.workflow_type == WorkflowType.BUILD:
            validation_section = Section(name="Release Validation")
            validation_step = Step(
                name="Classify remote versions",
                has_result=package.meta.remote_version is not None,
                ephemeral_status=package.meta.ephemeral.classify_remote_versions,
            )
            result.extend([validation_section, validation_step])

        result.extend(base_steps)
        return result


class DisplayModelClientImage(DisplayModelGeneric):
    """DisplayModel for client image packages."""

    def get_workflow_steps(
        self, package: Package, workflow: Workflow
    ) -> List[Union[Step, Section]]:
        """Get the list of workflow steps with client image specific steps prepended."""
        assert isinstance(
            package.meta, ClientImageMeta
        ), f"DisplayModelClientImage requires ClientImageMeta, got {type(package.meta).__name__}"

        result: List[Union[Step, Section]] = []
        base_steps = super().get_workflow_steps(package, workflow)

        validation_section = Section(name="Prerequisites")
        await_docker_image_step = Step(
            name="Await docker results",
            has_result=package.meta.base_image is not None,
            ephemeral_status=package.meta.ephemeral.await_docker_image,
        )
        validation_step = Step(
            name="Locate docker image",
            has_result=package.meta.base_image is not None,
            ephemeral_status=package.meta.ephemeral.validate_docker_image,
            message=package.meta.ephemeral.validate_docker_image_message,
        )
        result.extend([validation_section, await_docker_image_step, validation_step])

        result.extend(base_steps)
        return result


class DisplayModelClientTest(DisplayModelGeneric):
    """DisplayModel for client test packages."""

    def get_workflow_section(self, workflow: Workflow) -> Section:
        """Override to return 'Test Workflow' instead of 'Build Workflow'."""
        return Section(name="Test Workflow", is_workflow=True)

    def get_workflow_steps(
        self, package: Package, workflow: Workflow
    ) -> List[Union[Step, Section]]:
        """Get the list of workflow steps with client test specific steps prepended."""
        assert isinstance(
            package.meta, ClientTestMeta
        ), f"DisplayModelClientTest requires ClientTestMeta, got {type(package.meta).__name__}"

        result: List[Union[Step, Section]] = []
        base_steps = super().get_workflow_steps(package, workflow)

        prerequisites_section = Section(name="Prerequisites")
        await_client_image_step = Step(
            name="Await client image",
            has_result=package.meta.client_test_image is not None,
            ephemeral_status=package.meta.ephemeral.await_client_image,
        )
        locate_client_image_step = Step(
            name="Locate client image",
            has_result=package.meta.client_test_image is not None,
            ephemeral_status=package.meta.ephemeral.validate_client_image,
            message=package.meta.ephemeral.validate_client_image_message,
        )
        result.extend([prerequisites_section, await_client_image_step, locate_client_image_step])

        result.extend(base_steps)
        return result


def get_display_model(package_meta: PackageMeta) -> DisplayModelGeneric:
    """Factory function to get the appropriate DisplayModel for a package.

    Args:
        package_meta: The package metadata

    Returns:
        DisplayModel instance appropriate for the package type
    """
    # Return specialized DisplayModel for packages that require release validation
    if package_meta.package_type in (PackageType.HOMEBREW, PackageType.SNAP):
        return DisplayModelWithReleaseValidation()

    if package_meta.package_type == PackageType.CLIENTIMAGE:
        return DisplayModelClientImage()

    if package_meta.package_type == PackageType.CLIENTTEST:
        return DisplayModelClientTest()

    # Default DisplayModel for all other package types
    return DisplayModelGeneric()
