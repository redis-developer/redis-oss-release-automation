"""Redis OSS Release Automation CLI."""

import asyncio
import logging
import os
from typing import List, Optional

import typer
from py_trees.display import render_dot_tree, unicode_tree
from rich.console import Console
from rich.table import Table

from redis_release.bht.args import ReleaseArgs
from redis_release.bht.state import InMemoryStateStorage, S3StateStorage, StateSyncer

from .bht.tree import TreeInspector, async_tick_tock, initialize_tree_and_state
from .config import load_config
from .logging_config import setup_logging
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
        None, "--release-type", help="Override release type detection"
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
            raise typer.Exit(1)

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
        table.add_column("Publish Status", style="green")
        table.add_column("Build Artifacts", style="blue")
        table.add_column("Publish Artifacts", style="yellow")

        for pkg_type, pkg_state in state.packages.items():
            # Build status
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

            # Publish status
            if not pkg_state.publish_completed:
                publish_status = (
                    "[blue]In Progress[/blue]"
                    if pkg_state.publish_workflow
                    else "[dim]Not Started[/dim]"
                )
            elif pkg_state.publish_workflow and pkg_state.publish_workflow.conclusion:
                if pkg_state.publish_workflow.conclusion.value == "success":
                    publish_status = "[green]Success[/green]"
                elif pkg_state.publish_workflow.conclusion.value == "failure":
                    publish_status = "[red]Failed[/red]"
                else:
                    publish_status = "[yellow]Cancelled[/yellow]"
            else:
                publish_status = "[yellow]Cancelled[/yellow]"

            # Build artifacts
            if pkg_state.build_artifacts:
                build_artifacts = f"[green]{len(pkg_state.build_artifacts)}[/green]"
            else:
                build_artifacts = "[dim]None[/dim]"

            # Publish artifacts
            if pkg_state.publish_artifacts:
                publish_artifacts = f"[green]{len(pkg_state.publish_artifacts)}[/green]"
            else:
                publish_artifacts = "[dim]None[/dim]"

            table.add_row(
                pkg_type.value,
                build_status,
                publish_status,
                build_artifacts,
                publish_artifacts,
            )

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

    except Exception as e:
        console.print(f"[red] Failed to get status: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def release_print_bht(
    release_tag: str = typer.Argument(..., help="Release tag (e.g., 8.4-m01-int1)"),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file (default: config.yaml)"
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help=f"Name of specific PPA or tree branch to print. Available: {', '.join(TreeInspector.AVAILABLE_NAMES)}",
    ),
    only_packages: Optional[List[str]] = typer.Option(
        None,
        "--only-packages",
        help="Only process specific packages (can be specified multiple times)",
    ),
) -> None:
    """Print and render (using graphviz) the release behaviour tree or a specific PPA."""
    config_path = config_file or "config.yaml"
    config = load_config(config_path)

    # Create release args
    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=[],
        only_packages=only_packages or [],
    )
    setup_logging()

    if name:
        # Create TreeInspector and render the requested branch
        inspector = TreeInspector(release_tag=release_tag)

        try:
            branch = inspector.create_by_name(name)
            render_dot_tree(branch)
            print(unicode_tree(branch))
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)
    else:
        # Print full release tree
        with initialize_tree_and_state(
            config, args, InMemoryStateStorage(), read_only=True
        ) as (
            tree,
            _,
        ):
            render_dot_tree(tree.root)
            print(unicode_tree(tree.root))


@app.command()
def release_bht(
    release_tag: str = typer.Argument(..., help="Release tag (e.g., 8.4-m01-int1)"),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file (default: config.yaml)"
    ),
    force_rebuild: Optional[List[str]] = typer.Option(
        None,
        "--force-rebuild",
        help="Force rebuild for specific packages (can be specified multiple times). Use 'all' to force rebuild all packages.",
    ),
    only_packages: Optional[List[str]] = typer.Option(
        None,
        "--only-packages",
        help="Only process specific packages (can be specified multiple times)",
    ),
    tree_cutoff: int = typer.Option(
        2000, "--tree-cutoff", "-m", help="Max number of ticks to run the tree for"
    ),
) -> None:
    """Run release using behaviour tree implementation."""
    setup_logging()
    config_path = config_file or "config.yaml"
    config = load_config(config_path)

    # Create release args
    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=force_rebuild or [],
        only_packages=only_packages or [],
    )

    # Use context manager version with automatic lock management
    with initialize_tree_and_state(config, args) as (tree, _):
        asyncio.run(async_tick_tock(tree, cutoff=tree_cutoff))


@app.command()
def release_state(
    release_tag: str = typer.Argument(..., help="Release tag (e.g., 8.4-m01-int1)"),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file (default: config.yaml)"
    ),
) -> None:
    """Run release using behaviour tree implementation."""
    setup_logging(logging.INFO)
    config_path = config_file or "config.yaml"
    config = load_config(config_path)

    # Create release args
    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=[],
    )

    with StateSyncer(
        storage=S3StateStorage(),
        config=config,
        args=args,
    ):
        pass


if __name__ == "__main__":
    app()
