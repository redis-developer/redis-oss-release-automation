"""Redis OSS Release Automation CLI."""

import asyncio
import logging
import os
from typing import List, Optional

import aws_sso_lib
import typer
from py_trees.display import render_dot_tree, unicode_tree
from rich.console import Console
from rich.table import Table

from redis_release.bht.args import ReleaseArgs
from redis_release.bht.aws_auth import AwsState, print_credentials_as_env_vars
from redis_release.bht.state import (
    InMemoryStateStorage,
    Package,
    PackageMeta,
    ReleaseMeta,
    ReleaseState,
    S3StateStorage,
    StateSyncer,
    Workflow,
)

from .bht.ppas import (
    create_download_artifacts_ppa,
    create_extract_artifact_result_ppa,
    create_find_workflow_by_uuid_ppa,
    create_identify_target_ref_ppa,
    create_trigger_workflow_ppa,
    create_workflow_completion_ppa,
    create_workflow_success_ppa,
)
from .bht.tree import (
    async_tick_tock,
    create_build_workflow_tree_branch,
    create_extract_result_tree_branch,
    create_publish_workflow_tree_branch,
    create_workflow_complete_tree_branch,
    create_workflow_with_result_tree_branch,
    initialize_tree_and_state,
)
from .config import load_config
from .github_client_async import GitHubClientAsync
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
        help="Name of specific PPA or tree branch to print. PPAs: 'workflow_success', 'workflow_completion', 'find_workflow', 'trigger_workflow', 'identify_target_ref', 'download_artifacts', 'extract_artifact_result'. Tree branches: 'workflow_success_branch', 'workflow_result_branch'",
    ),
) -> None:
    """Print and render (using graphviz) the release behaviour tree or a specific PPA."""
    config_path = config_file or "config.yaml"
    config = load_config(config_path)

    # Create release args
    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=[],
    )

    if name:
        # Print specific PPA or tree branch
        github_client = GitHubClientAsync(token=os.getenv("GITHUB_TOKEN", "dummy"))

        # Create mock state objects for PPA creation
        workflow = Workflow(workflow_file="test.yml", inputs={})
        package_meta = PackageMeta(repo="redis/redis", ref="main")
        release_meta = ReleaseMeta(tag=release_tag)
        log_prefix = "test"

        # Create mock ReleaseState for tree branch functions
        package = Package(
            meta=package_meta,
            build=workflow,
            publish=Workflow(workflow_file="publish.yml", inputs={}),
        )
        state = ReleaseState(meta=release_meta, packages={"docker": package})

        # Map PPA names to creation functions
        ppa_creators = {
            "workflow_success": lambda: create_workflow_success_ppa(
                workflow, log_prefix
            ),
            "workflow_completion": lambda: create_workflow_completion_ppa(
                workflow, package_meta, github_client, log_prefix
            ),
            "find_workflow": lambda: create_find_workflow_by_uuid_ppa(
                workflow, package_meta, github_client, log_prefix
            ),
            "trigger_workflow": lambda: create_trigger_workflow_ppa(
                workflow, package_meta, release_meta, github_client, log_prefix
            ),
            "identify_target_ref": lambda: create_identify_target_ref_ppa(
                package_meta, release_meta, github_client, log_prefix
            ),
            "download_artifacts": lambda: create_download_artifacts_ppa(
                workflow, package_meta, github_client, log_prefix
            ),
            "extract_artifact_result": lambda: create_extract_artifact_result_ppa(
                "test-artifact", workflow, package_meta, github_client, log_prefix
            ),
            # Tree branch functions
            "workflow_complete_branch": lambda: create_workflow_complete_tree_branch(
                workflow, package_meta, release_meta, github_client, ""
            ),
            "workflow_with_result_branch": lambda: create_workflow_with_result_tree_branch(
                "artifact", workflow, package_meta, release_meta, github_client, ""
            ),
            "publish_worflow_branch": lambda: create_publish_workflow_tree_branch(
                workflow, workflow, package_meta, release_meta, github_client, ""
            ),
            "build_workflow_branch": lambda: create_build_workflow_tree_branch(
                workflow, package_meta, release_meta, github_client, ""
            ),
        }

        if name not in ppa_creators:
            console.print(
                f"[red]Error: Unknown name '{name}'. Available options: {', '.join(ppa_creators.keys())}[/red]"
            )
            raise typer.Exit(1)

        ppa = ppa_creators[name]()
        render_dot_tree(ppa)
        print(unicode_tree(ppa))
    else:
        # Print full release tree
        with initialize_tree_and_state(config, args, InMemoryStateStorage()) as (
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
) -> None:
    """Run release using behaviour tree implementation."""
    setup_logging(logging.DEBUG)
    config_path = config_file or "config.yaml"
    config = load_config(config_path)

    # Create release args
    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=force_rebuild or [],
    )

    # Use context manager version with automatic lock management
    with initialize_tree_and_state(config, args) as (tree, _):
        asyncio.run(async_tick_tock(tree))


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


@app.command()
def sso(
    print_tree: bool = typer.Option(False, "-p", help="Print tree to console")
) -> None:
    import threading

    import janus

    from .bht.aws_auth import create_aws_tree
    from .sso_ui import run_sso_ui

    setup_logging(logging.DEBUG, log_file="sso_debug.log")
    logger = logging.getLogger(__name__)

    # Create janus queues for bidirectional communication
    tree_to_ui = janus.Queue()
    ui_to_tree = janus.Queue()

    aws_state = AwsState(ui_to_tree=ui_to_tree.sync_q)
    tree = create_aws_tree(aws_state, tree_to_ui.sync_q, ui_to_tree.async_q)
    if print_tree:
        render_dot_tree(tree.root)
        print(unicode_tree(tree.root))
        return

    def run_tree() -> None:
        asyncio.run(async_tick_tock(tree))

    tree_thread = threading.Thread(target=run_tree)
    tree_thread.daemon = True
    tree_thread.start()

    # Wait for the tree to send start message
    msg = tree_to_ui.sync_q.get()
    if type(msg) == str and msg == "start":
        run_sso_ui(tree_to_ui.async_q, ui_to_tree.sync_q)
    elif type(msg) == str and msg == "shutdown":
        # Tree already done (there are valid creds), no need to run UI
        logger.debug("Received shutdown command, exiting app")
    else:
        logger.error(f"Unknown message received: {msg}")

    tree_thread.join()
    tree_to_ui.close()
    ui_to_tree.close()

    if aws_state.credentials:
        print_credentials_as_env_vars(aws_state.credentials)


if __name__ == "__main__":
    app()
