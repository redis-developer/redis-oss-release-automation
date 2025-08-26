"""Redis OSS Release Automation CLI."""

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .models import ReleaseType
from .orchestrator import ReleaseOrchestrator

app = typer.Typer(
    name="redis-release",
    help="Redis OSS Release Automation CLI",
    add_completion=False,
)

console = Console()


def get_orchestrator(
    github_token: Optional[str] = None,
    require_github_token: bool = True,
) -> ReleaseOrchestrator:
    """Create and return a ReleaseOrchestrator instance."""
    if require_github_token:
        if not github_token:
            github_token = os.getenv("GITHUB_TOKEN")
            if not github_token:
                console.print(
                    "[red]Error: GITHUB_TOKEN environment variable is required[/red]"
                )
                raise typer.Exit(1)
    else:
        # for commands that don't need GitHub API access
        github_token = github_token or os.getenv("GITHUB_TOKEN", "not-required")

    return ReleaseOrchestrator(
        github_token=github_token,
    )


@app.command()
def release(
    tag: str = typer.Argument(..., help="Release tag (e.g., 8.4-m01-int1)"),
    force_rebuild: bool = typer.Option(
        False,
        "--force-rebuild",
        help="Force rebuild Docker image, ignoring existing state",
    ),
    release_type: ReleaseType = typer.Option(
        ReleaseType.AUTO, "--release-type", help="Override release type detection"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be done without executing"
    ),
    github_token: Optional[str] = typer.Option(
        None, "--github-token", envvar="GITHUB_TOKEN", help="GitHub API token"
    ),
) -> None:
    """Execute release workflow for the given tag."""
    console.print(f"[bold blue]Starting release process for tag: {tag}[/bold blue]")

    if dry_run:
        console.print("[yellow]DRY RUN MODE - No actual changes will be made[/yellow]")

    orchestrator = get_orchestrator(github_token)

    try:
        result = orchestrator.execute_release(
            tag=tag,
            force_rebuild=force_rebuild,
            release_type=release_type,
            dry_run=dry_run,
        )

        if result.success:
            console.print(f"[green] Release {tag} completed successfully![/green]")
        else:
            console.print(
                f"[yellow] Release {tag} requires manual intervention[/yellow]"
            )
            if result.message:
                console.print(f"[yellow]{result.message}[/yellow]")

    except Exception as e:
        console.print(f"[red] Release {tag} failed: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def status(
    tag: str = typer.Argument(..., help="Release tag to check status for"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Use local cache instead of S3"
    ),
) -> None:
    """Show release status for the given tag."""
    console.print(f"[bold blue]Release status for tag: {tag}[/bold blue]")

    orchestrator = get_orchestrator(github_token=None, require_github_token=False)

    try:
        state = orchestrator.get_release_status(tag, dry_run=dry_run)

        if not state:
            console.print(f"[yellow]No release found for tag: {tag}[/yellow]")
            return

        table = Table(title=f"Release Status: {tag}")
        table.add_column("Package", style="cyan")
        table.add_column("Build Status", style="magenta")
        table.add_column("Artifacts", style="blue")

        for pkg_type, pkg_state in state.packages.items():
            if not pkg_state.build_completed:
                build_status = "[blue]In Progress[/blue]"
            elif pkg_state.build_workflow and pkg_state.build_workflow.conclusion:
                if pkg_state.build_workflow.conclusion.value == "success":
                    build_status = "[green]Success[/green]"
                elif pkg_state.build_workflow.conclusion.value == "failure":
                    build_status = "[red]Failed[/red]"
                else:
                    build_status = "[yellow]Cancelled[/yellow]"
            else:
                build_status = "[yellow]Cancelled[/yellow]"

            if pkg_state.artifact_urls:
                artifacts = f"[green]{len(pkg_state.artifact_urls)} artifacts[/green]"
            else:
                artifacts = "[dim]None[/dim]"

            table.add_row(pkg_type.value, build_status, artifacts)

        console.print(table)

        if state.redis_tag_commit or state.docker_repo_commit:
            console.print("\n[bold]Commit Information:[/bold]")
            if state.redis_tag_commit:
                console.print(
                    f"  Redis tag {tag}: [cyan]{state.redis_tag_commit[:8]}[/cyan]"
                )
            if state.docker_repo_commit:
                console.print(
                    f"  Docker repo: [cyan]{state.docker_repo_commit[:8]}[/cyan]"
                )

        if state.is_build_phase_complete():
            console.print("[green]Release is complete![/green]")
        elif state.has_build_failures():
            console.print(
                "[red]Release failed - build phase completed with errors[/red]"
            )
        elif state.is_build_phase_finished():
            console.print("[yellow]Release completed but not successful[/yellow]")
        else:
            console.print("[blue]Building Docker image...[/blue]")

    except Exception as e:
        console.print(f"[red] Failed to get status: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
