"""Workflow execution classes for Redis release automation."""
import re
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from rich.console import Console

from .github_client import GitHubClient
from .models import (
    PackageState,
    PackageType,
    ReleaseState,
    WorkflowConclusion,
    WorkflowRun,
)

console = Console()


class Phase(ABC):
    """Abstract base class for workflow phases."""

    def __init__(
        self,
        state: ReleaseState,
        repo: str,
        orchestrator_config: Dict[str, Any],
        timeout_minutes: int = 45,
    ):
        self.state = state
        self.repo = repo
        self.orchestrator_config = orchestrator_config
        self.timeout_minutes = timeout_minutes

    @property
    @abstractmethod
    def phase_name(self) -> str:
        """Human-readable phase name for logging."""
        pass

    @property
    @abstractmethod
    def package_state(self) -> PackageState:
        """Get the package state for this phase."""
        pass

    @property
    @abstractmethod
    def branch(self) -> str:
        """Get the branch to run the workflow on."""
        pass

    @property
    @abstractmethod
    def workflow_file(self) -> str:
        """Get the workflow file name."""
        pass

    @property
    @abstractmethod
    def workflow_inputs(self) -> Dict[str, Any]:
        """Get the inputs to pass to the workflow."""
        pass

    @abstractmethod
    def is_completed(self) -> bool:
        """Check if this phase is already completed."""
        pass

    @abstractmethod
    def get_workflow(self) -> Optional[WorkflowRun]:
        """Get the current workflow for this phase."""
        pass

    @abstractmethod
    def set_workflow(self, workflow: WorkflowRun) -> None:
        """Set the workflow for this phase."""
        pass

    @abstractmethod
    def set_completed(self, completed: bool) -> None:
        """Mark this phase as completed."""
        pass

    @abstractmethod
    def set_artifacts(self, artifacts: Dict[str, Dict[str, Any]]) -> None:
        """Set artifacts for this phase."""
        pass

    @abstractmethod
    def set_result(self, result_data: Dict[str, Any]) -> None:
        """Set phase-specific result data."""
        pass

    @abstractmethod
    def extract_result(self, github_client: GitHubClient, artifacts: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Extract phase-specific result data from artifacts."""
        pass

    def _get_release_branch(self) -> str:
        """Get the release branch based on the release tag.

        Extracts major.minor from tag (e.g., "8.2.1" -> "release/8.2", "8.4-m01-int" -> "release/8.4").

        Returns:
            Release branch name

        Raises:
            ValueError: If tag format is invalid
        """
        # Extract major.minor version from the beginning of the tag
        # This handles both "8.2.1" and "8.4-m01-int" formats
        match = re.match(r"^(\d+)\.(\d+)", self.state.tag)
        if not match:
            raise ValueError(
                f"Invalid tag format '{self.state.tag}': expected tag to start with major.minor version (e.g., '8.2.1' or '8.4-m01')"
            )

        major = match.group(1)
        minor = match.group(2)
        major_minor = f"{major}.{minor}"
        return f"release/{major_minor}"


class BuildPhase(Phase):
    """Build phase implementation."""

    @property
    def phase_name(self) -> str:
        return "Docker build"

    @property
    def package_state(self) -> PackageState:
        return self.state.packages[PackageType.DOCKER]

    @property
    def branch(self) -> str:
        """Get the Docker branch based on the release tag."""
        return self._get_release_branch()

    @property
    def workflow_file(self) -> str:
        """Get the build workflow file from orchestrator config."""
        return self.orchestrator_config.get("workflow", "release_build_and_test.yml")

    @property
    def workflow_inputs(self) -> Dict[str, Any]:
        """Get the build workflow inputs."""
        return {
            "release_tag": self.state.tag,
        }

    def is_completed(self) -> bool:
        return self.package_state.build_completed

    def get_workflow(self) -> Optional[WorkflowRun]:
        return self.package_state.build_workflow

    def set_workflow(self, workflow: WorkflowRun) -> None:
        self.package_state.build_workflow = workflow

    def set_completed(self, completed: bool) -> None:
        self.package_state.build_completed = completed

    def set_artifacts(self, artifacts: Dict[str, Dict[str, Any]]) -> None:
        self.package_state.build_artifacts = artifacts

    def set_result(self, result_data: Dict[str, Any]) -> None:
        self.package_state.release_handle = result_data

    def extract_result(self, github_client: GitHubClient, artifacts: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Extract release_handle from artifacts."""
        result = github_client.extract_result(self.repo, artifacts, "release_handle", "release_handle.json")
        if result is None:
            console.print("[red]Failed to extract release_handle from artifacts[/red]")
        return result


class PublishPhase(Phase):
    """Publish phase implementation."""

    @property
    def phase_name(self) -> str:
        return "Docker publish"

    @property
    def package_state(self) -> PackageState:
        return self.state.packages[PackageType.DOCKER]

    @property
    def branch(self) -> str:
        """Get the Docker branch based on the release tag."""
        return self._get_release_branch()

    @property
    def workflow_file(self) -> str:
        """Get the publish workflow file from orchestrator config."""
        return self.orchestrator_config.get("publish_workflow", "release_publish.yml")

    @property
    def workflow_inputs(self) -> Dict[str, Any]:
        """Get the publish workflow inputs.

        Raises:
            RuntimeError: If release_handle is not available in package state
        """
        if not self.package_state.release_handle:
            raise RuntimeError("release_handle is required for publish phase but not found in package state")

        return {
            "release_handle": json.dumps(self.package_state.release_handle),
        }

    def is_completed(self) -> bool:
        return self.package_state.publish_completed

    def get_workflow(self) -> Optional[WorkflowRun]:
        return self.package_state.publish_workflow

    def set_workflow(self, workflow: WorkflowRun) -> None:
        self.package_state.publish_workflow = workflow

    def set_completed(self, completed: bool) -> None:
        self.package_state.publish_completed = completed

    def set_artifacts(self, artifacts: Dict[str, Dict[str, Any]]) -> None:
        self.package_state.publish_artifacts = artifacts

    def set_result(self, result_data: Dict[str, Any]) -> None:
        self.package_state.publish_info = result_data

    def extract_result(self, github_client: GitHubClient, artifacts: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Extract release_info from artifacts."""
        result = github_client.extract_result(self.repo, artifacts, "release_info", "release_info.json")
        if result is None:
            console.print("[red]Failed to extract release_info from artifacts[/red]")
        return result


class PhaseExecutor:
    """Executes workflow phases."""

    def execute_phase(self, phase: Phase, github_client: GitHubClient) -> bool:
        """Execute a workflow phase.

        Args:
            phase: The phase to execute
            github_client: GitHub client for API operations

        Returns:
            True if phase succeeded, False otherwise
        """
        if not self._trigger_workflow(phase, github_client):
            return False

        # Wait for workflow completion if needed
        workflow = phase.get_workflow()
        console.print("[dim]Waiting for workflow completion...[/dim]")
        return self._wait_for_completion(phase, github_client, workflow)

    def _trigger_workflow(self, phase: Phase, github_client: GitHubClient) -> bool:
        """Trigger the workflow for a phase."""
        console.print(f"[dim]Using branch: {phase.branch}[/dim]")

        if not github_client.check_workflow_exists(phase.repo, phase.workflow_file):
            console.print(
                f"[red]Workflow '{phase.workflow_file}' not found in {phase.repo}[/red]"
            )
            console.print(
                f"[yellow]Make sure the workflow file exists in branch '{phase.branch}'[/yellow]"
            )
            return False

        try:
            workflow_run = github_client.trigger_workflow(
                phase.repo, phase.workflow_file, phase.workflow_inputs, ref=phase.branch
            )
            phase.set_workflow(workflow_run)
            return True

        except Exception as e:
            console.print(f"[red]Failed to trigger {phase.phase_name}: {e}[/red]")
            return False

    def _wait_for_completion(self, phase: Phase, github_client: GitHubClient, workflow: WorkflowRun) -> bool:
        """Wait for workflow completion and handle results."""
        try:
            console.print(f"[blue]Waiting for {phase.phase_name} to complete...[/blue]")
            completed_run = github_client.wait_for_workflow_completion(
                workflow.repo,
                workflow.run_id,
                timeout_minutes=phase.timeout_minutes,
            )

            phase.set_workflow(completed_run)

            if completed_run.conclusion == WorkflowConclusion.SUCCESS:
                return self._handle_success(phase, github_client, completed_run)
            elif completed_run.conclusion == WorkflowConclusion.FAILURE:
                phase.set_completed(True)  # completed, but failed
                console.print(f"[red]{phase.phase_name} failed[/red]")
                return False
            else:
                return self._handle_other_conclusion(phase, completed_run)

        except Exception as e:
            console.print(f"[red]{phase.phase_name} failed: {e}[/red]")
            return False

    def _handle_success(self, phase: Phase, github_client: GitHubClient, completed_run: WorkflowRun) -> bool:
        """Handle successful workflow completion."""
        phase.set_completed(True)

        # Get artifacts
        artifacts = github_client.get_workflow_artifacts(
            completed_run.repo, completed_run.run_id
        )
        phase.set_artifacts(artifacts)

        # Extract phase-specific result data
        result_data = phase.extract_result(github_client, artifacts)
        if result_data is None:
            return False

        phase.set_result(result_data)
        console.print(f"[green]{phase.phase_name} completed successfully[/green]")
        return True

    def _handle_other_conclusion(self, phase: Phase, completed_run: WorkflowRun) -> bool:
        """Handle non-success, non-failure conclusions."""
        phase.set_completed(True)  # completed, but not successful
        conclusion_text = (
            completed_run.conclusion.value
            if completed_run.conclusion
            else "cancelled/skipped"
        )

        if conclusion_text in ["cancelled", "cancelled/skipped"]:
            status_color = "yellow"
        elif conclusion_text in ["skipped"]:
            status_color = "blue"
        else:
            status_color = "red"

        console.print(
            f"[dim]{phase.phase_name} completed with status:[/dim] [{status_color}]{conclusion_text}[/{status_color}]"
        )
        return False
