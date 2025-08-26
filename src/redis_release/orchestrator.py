"""Main orchestration logic for Redis release automation."""

from dataclasses import dataclass
from typing import Optional

from rich.console import Console

from .github_client import GitHubClient
from .models import (
    PackageState,
    PackageType,
    ReleaseState,
    ReleaseType,
    WorkflowConclusion,
)
from .state_manager import StateManager

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
            "repo": "Peter-Sh/docker-library-redis",
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
        # extract major.minor version from tag
        # examples: "8.2.1" -> "8.2", "8.4-m01" -> "8.4"
        if "." in tag:
            parts = tag.split(".")
            if len(parts) >= 2:
                major_minor = f"{parts[0]}.{parts[1]}"
                return f"release/{major_minor}"

        console.print(
            f"[yellow]Warning: Could not determine branch for tag '{tag}', using 'main'[/yellow]"
        )
        return "main"

    def _create_initial_state(
        self,
        tag: str,
        release_type: ReleaseType,
        force_rebuild: bool,
        github_client: GitHubClient = None,
    ) -> ReleaseState:
        """Create initial release state."""
        state = ReleaseState(
            tag=tag,
            release_type=release_type,
            force_rebuild=force_rebuild,
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
                        tag, actual_release_type, force_rebuild, github_client
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

            if self._should_run_build_phase(state):
                console.print("[bold blue] Starting build phase[/bold blue]")
                build_result = self._execute_build_phase(state, github_client)
                if not build_result:
                    state_manager.save_state(state)
                    return ReleaseResult(
                        success=False, message="Build phase failed", state=state
                    )
            else:
                docker_state = state.packages.get(PackageType.DOCKER)
                if docker_state and docker_state.build_completed:
                    if (
                        docker_state.build_workflow
                        and docker_state.build_workflow.conclusion
                    ):
                        conclusion = docker_state.build_workflow.conclusion.value
                        if conclusion == "success":
                            console.print(
                                "[green] Build phase already completed successfully[/green]"
                            )
                            console.print(
                                "[dim]   Skipping workflow execution - Docker build is done[/dim]"
                            )
                        else:
                            console.print(
                                f"[yellow] Build phase already completed with status: {conclusion}[/yellow]"
                            )
                            console.print(
                                "[dim]   Skipping workflow execution - use --force-rebuild to retry[/dim]"
                            )
                    else:
                        console.print("[yellow] Build phase already completed[/yellow]")
                        console.print(
                            "[dim]   Skipping workflow execution - use --force-rebuild to retry[/dim]"
                        )
                else:
                    console.print("[blue] No build phase needed[/blue]")

            state_manager.save_state(state)

            if state.is_build_phase_complete():
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
            state_manager.release_lock(tag, lock_owner)

    def _should_run_build_phase(self, state: ReleaseState) -> bool:
        """Check if build phase should be executed."""
        if state.force_rebuild:
            return True

        docker_state = state.packages.get(PackageType.DOCKER)
        return not docker_state or not docker_state.build_completed

    def _execute_build_phase(
        self, state: ReleaseState, github_client: GitHubClient
    ) -> bool:
        """Execute build phase for all packages.

        Returns:
            True if all builds succeeded
        """
        docker_state = state.packages[PackageType.DOCKER]

        if docker_state.build_completed and not state.force_rebuild:
            console.print("[yellow]Skipping Docker - already built[/yellow]")
        else:
            repo = self._get_docker_repo()
            workflow_file = self.docker_config["workflow"]
            branch = self._get_docker_branch(state.tag)

            inputs = {
                "release_tag": state.tag,
            }

            console.print(f"[dim]Using branch: {branch}[/dim]")

            if not github_client.check_workflow_exists(repo, workflow_file):
                console.print(
                    f"[red]Workflow '{workflow_file}' not found in {repo}[/red]"
                )
                console.print(
                    f"[yellow]Make sure the workflow file exists in branch '{branch}'[/yellow]"
                )
                return False

            try:
                workflow_run = github_client.trigger_workflow(
                    repo, workflow_file, inputs, ref=branch
                )
                docker_state.build_workflow = workflow_run

            except Exception as e:
                console.print(f"[red]Failed to trigger Docker build: {e}[/red]")
                return False

        if docker_state.build_workflow and not docker_state.build_completed:
            try:
                console.print("[blue]Waiting for Docker build to complete...[/blue]")
                completed_run = github_client.wait_for_workflow_completion(
                    docker_state.build_workflow.repo,
                    docker_state.build_workflow.run_id,
                    timeout_minutes=45,
                )

                docker_state.build_workflow = completed_run

                if completed_run.conclusion == WorkflowConclusion.SUCCESS:
                    docker_state.build_completed = True
                    artifacts = github_client.get_workflow_artifacts(
                        completed_run.repo, completed_run.run_id
                    )
                    docker_state.artifact_urls = artifacts
                    console.print("[green]Docker build completed successfully[/green]")
                elif completed_run.conclusion == WorkflowConclusion.FAILURE:
                    docker_state.build_completed = True  # completed, but failed
                    console.print("[red]Docker build failed[/red]")
                    return False
                else:
                    docker_state.build_completed = True  # completed, but not successful
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
                        f"[dim]Docker build completed with status:[/dim] [{status_color}]{conclusion_text}[/{status_color}]"
                    )
                    return False

            except Exception as e:
                console.print(f"[red]Docker build failed: {e}[/red]")
                return False

        return True

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
