"""Main orchestration logic for Redis release automation."""
import re
from dataclasses import dataclass
from typing import Optional

from rich.console import Console

from .github_client import GitHubClient
from .models import (
    PackageState,
    PackageType,
    ReleaseState,
    ReleaseType,
    WorkflowRun,
)
from .state_manager import StateManager
from .workflow_executor import BuildPhase, PhaseExecutor, PublishPhase

console = Console()


@dataclass
class ReleaseResult:
    """Result of a release operation."""

    success: bool
    message: Optional[str] = None
    state: Optional[ReleaseState] = None


class ReleaseOrchestrator:
    """Main orchestrator for Redis release automation."""

    def __init__(
        self,
        github_token: str,
        state_bucket: Optional[str] = None,
    ):
        """Initialize release orchestrator.

        Args:
            github_token: GitHub API token
            state_bucket: S3 bucket for state storage
        """
        self.github_token = github_token
        self.state_bucket = state_bucket

        self._github_client: Optional[GitHubClient] = None
        self._state_manager: Optional[StateManager] = None

        self.docker_config = {
            "repo": "redis/docker-library-redis",
            "workflow": "release_build_and_test.yml",
        }

    def _get_github_client(self, dry_run: bool = False) -> GitHubClient:
        """Get GitHub client instance."""
        if self._github_client is None or self._github_client.dry_run != dry_run:
            self._github_client = GitHubClient(self.github_token, dry_run=dry_run)
        return self._github_client

    def _get_state_manager(self, dry_run: bool = False) -> StateManager:
        """Get state manager instance."""
        if self._state_manager is None or self._state_manager.dry_run != dry_run:
            self._state_manager = StateManager(
                bucket_name=self.state_bucket,
                dry_run=dry_run,
            )
        return self._state_manager

    def _determine_release_type(
        self, tag: str, override: ReleaseType = ReleaseType.AUTO
    ) -> ReleaseType:
        """Determine release type from tag name."""
        if override != ReleaseType.AUTO:
            return override

        if tag.endswith(tuple(f"-int{i}" for i in range(1, 100))):
            return ReleaseType.PRIVATE

        return ReleaseType.PUBLIC

    def _get_docker_repo(self) -> str:
        """Get Docker repository name."""
        return self.docker_config["repo"]

    def _get_docker_branch(self, tag: str) -> str:
        """Determine the correct branch for Docker workflow based on release tag.

        Args:
            tag: Release tag (e.g., "8.2.1", "8.4-m01")

        Returns:
            Branch name to use for workflow trigger
        """
        # Extract major.minor version from tag
        # examples: "8.2.1" -> "8.2", "8.4-m01" -> "8.4"
        match = re.match(r"^(\d+)\.(\d+)", tag)
        if match:
            major = match.group(1)
            minor = match.group(2)
            major_minor = f"{major}.{minor}"
            return f"release/{major_minor}"

        console.print(
            f"[yellow]Warning: Could not determine branch for tag '{tag}', using 'main'[/yellow]"
        )
        return "main"

    def _create_initial_state(
        self,
        tag: str,
        release_type: ReleaseType,
        github_client: GitHubClient = None,
    ) -> ReleaseState:
        """Create initial release state."""
        state = ReleaseState(
            tag=tag,
            release_type=release_type,
        )

        if github_client:
            console.print("[dim]Getting commit information...[/dim]")

            redis_commit = github_client.get_tag_commit("redis/redis", tag)
            if redis_commit:
                state.redis_tag_commit = redis_commit
                console.print(f"[dim]  Redis tag {tag}: {redis_commit[:8]}[/dim]")
            else:
                raise ValueError(
                    f"Redis tag '{tag}' not found in redis/redis repository. Cannot proceed with release."
                )

            docker_branch = self._get_docker_branch(tag)
            docker_commit = github_client.get_branch_latest_commit(
                self._get_docker_repo(), docker_branch
            )
            if docker_commit:
                state.docker_repo_commit = docker_commit
                console.print(
                    f"[dim]  Docker repo {docker_branch}: {docker_commit[:8]}[/dim]"
                )
            else:
                console.print(
                    f"[yellow]Warning: Could not get latest commit from {docker_branch}[/yellow]"
                )

        state.packages[PackageType.DOCKER] = PackageState(
            package_type=PackageType.DOCKER
        )

        return state

    def execute_release(
        self,
        tag: str,
        force_rebuild: bool = False,
        release_type: ReleaseType = ReleaseType.AUTO,
        dry_run: bool = False,
    ) -> ReleaseResult:
        """Execute the main release workflow.

        Args:
            tag: Release tag
            force_rebuild: Force rebuild all packages
            clients_test_passed: Whether client testing is complete
            release_type: Override release type detection
            packages: Only process specific packages
            dry_run: Simulate operations without making changes

        Returns:
            ReleaseResult with operation outcome
        """
        console.print(f"[bold blue] Starting release process for {tag}[/bold blue]")

        github_client = self._get_github_client(dry_run)
        state_manager = self._get_state_manager(dry_run)

        actual_release_type = self._determine_release_type(tag, release_type)
        console.print(f"[blue]Release type: {actual_release_type.value}[/blue]")

        # use release tag as lock identifier to prevent concurrent releases
        lock_owner = f"release-{tag}"

        try:
            if not state_manager.acquire_lock(tag, lock_owner):
                return ReleaseResult(
                    success=False,
                    message="Could not acquire lock - another release process may be running",
                )

            state = state_manager.load_state(tag)
            if state is None or force_rebuild:
                console.print("[blue]Creating new release state[/blue]")
                try:
                    state = self._create_initial_state(
                        tag, actual_release_type, github_client
                    )
                except ValueError as e:
                    console.print(f"[red]Release validation failed: {e}[/red]")
                    return ReleaseResult(
                        success=False,
                        message=str(e),
                        state=None,
                    )
            else:
                console.print("[blue]Loaded existing release state[/blue]")

            if force_rebuild or self._should_run_build_phase(state):
                console.print("[bold blue] Starting build phase[/bold blue]")
                build_result = self._execute_build_phase(state, github_client)
                if not build_result:
                    state_manager.save_state(state)
                    return ReleaseResult(
                        success=False, message="Build phase failed", state=state
                    )
            else:
                docker_state = state.packages.get(PackageType.DOCKER)
                self._print_completed_state_phase(
                    phase_completed=docker_state.build_completed if docker_state else False,
                    workflow=docker_state.build_workflow if docker_state else None,
                    name="Build",
                )

            state_manager.save_state(state)

            # Execute publish phase if needed
            if force_rebuild or self._should_run_publish_phase(state):
                console.print("[blue]Starting publish phase...[/blue]")
                if not self._execute_publish_phase(state, github_client):
                    return ReleaseResult(
                        success=False, message="Publish phase failed", state=state
                    )
            else:
                docker_state = state.packages.get(PackageType.DOCKER)
                self._print_completed_state_phase(
                    phase_completed=docker_state.publish_completed if docker_state else False,
                    workflow=docker_state.publish_workflow if docker_state else None,
                    name="Publish",
                )

            state_manager.save_state(state)

            if state.is_build_successful() and state.is_publish_successful():
                return ReleaseResult(
                    success=True,
                    message=f"Release {tag} completed successfully!",
                    state=state,
                )
            else:
                return ReleaseResult(
                    success=False, message=f"Release {tag} failed", state=state
                )

        finally:
            state_manager.save_state(state)
            state_manager.release_lock(tag, lock_owner)

    def _should_run_build_phase(self, state: ReleaseState) -> bool:
        """Check if build phase should be executed."""
        docker_state = state.packages.get(PackageType.DOCKER)
        return not docker_state or not docker_state.is_build_phase_successful()

    def _should_run_publish_phase(self, state: ReleaseState) -> bool:
        """Check if publish phase should be executed."""
        # Only run publish phase if build phase is complete
        docker_state = state.packages.get(PackageType.DOCKER)
        docker_state = state.packages.get(PackageType.DOCKER)
        if not docker_state or not docker_state.is_publish_phase_successful():
            return state.release_type == ReleaseType.PUBLIC

    def _print_completed_state_phase(
        self,
        phase_completed: bool,
        workflow: Optional[WorkflowRun],
        name: str
    ) -> None:
        """Print the current phase state when phase is already completed."""
        if phase_completed:
            if workflow and workflow.conclusion:
                conclusion = workflow.conclusion.value
                if conclusion == "success":
                    console.print(
                        f"[green] {name} phase already completed successfully[/green]"
                    )
                    console.print(
                        f"[dim]   Skipping workflow execution - {name} is done[/dim]"
                    )
                else:
                    console.print(
                        f"[yellow] {name} phase already completed with status: {conclusion}[/yellow]"
                    )
                    console.print(
                        "[dim]   Skipping workflow execution - use --force-rebuild to retry[/dim]"
                    )
            else:
                console.print(f"[yellow] {name} phase already completed[/yellow]")
                console.print(
                    "[dim]   Skipping workflow execution - use --force-rebuild to retry[/dim]"
                )
        else:
            console.print(f"[blue] No {name.lower()} phase needed[/blue]")

    def _execute_build_phase(
        self, state: ReleaseState, github_client: GitHubClient
    ) -> bool:
        """Execute build phase for all packages.

        Returns:
            True if all builds succeeded
        """
        repo = self._get_docker_repo()

        build_phase = BuildPhase(
            state=state,
            repo=repo,
            orchestrator_config=self.docker_config,
            timeout_minutes=45,
        )

        executor = PhaseExecutor()
        return executor.execute_phase(build_phase, github_client)

    def _execute_publish_phase(
        self, state: ReleaseState, github_client: GitHubClient
    ) -> bool:
        """Execute publish phase for all packages.

        Returns:
            True if all publishes succeeded

        Raises:
            RuntimeError: If release_handle doesn't exist in state (raised by PublishPhase)
        """
        repo = self._get_docker_repo()

        publish_phase = PublishPhase(
            state=state,
            repo=repo,
            orchestrator_config=self.docker_config,
            timeout_minutes=30,  # Publish might be faster than build
        )

        executor = PhaseExecutor()
        return executor.execute_phase(publish_phase, github_client)

    def get_release_status(
        self, tag: str, dry_run: bool = False
    ) -> Optional[ReleaseState]:
        """Get current release status.

        Args:
            tag: Release tag
            dry_run: Use local cache instead of S3

        Returns:
            ReleaseState or None if not found
        """
        state_manager = self._get_state_manager(dry_run=dry_run)
        return state_manager.load_state(tag)
