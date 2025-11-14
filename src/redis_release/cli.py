"""Redis OSS Release Automation CLI."""

import asyncio
import logging
from typing import List, Optional

import typer
from py_trees.display import render_dot_tree, unicode_tree

from redis_release.models import ReleaseType
from redis_release.state_display import print_state_table
from redis_release.state_manager import (
    InMemoryStateStorage,
    S3StateStorage,
    StateManager,
)
from redis_release.state_slack import init_slack_printer

from .bht.tree import TreeInspector, async_tick_tock, initialize_tree_and_state
from .config import load_config
from .logging_config import setup_logging
from .models import ReleaseArgs

app = typer.Typer(
    name="redis-release",
    help="Redis OSS Release Automation CLI",
    add_completion=False,
)

logger = logging.getLogger(__name__)


@app.command()
def release_print(
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
            logger.error(f"[red]Error: {e}[/red]")
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
def release(
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
    force_release_type: Optional[ReleaseType] = typer.Option(
        None,
        "--force-release-type",
        help="Force release type (public or internal)",
    ),
    override_state_name: Optional[str] = typer.Option(
        None,
        "--override-state-name",
        help="Custom state name to use instead of release tag, to be able to make test runs without affecting production state",
    ),
    slack_token: Optional[str] = typer.Option(
        None,
        "--slack-token",
        help="Slack bot token (if not provided, uses SLACK_BOT_TOKEN env var)",
    ),
    slack_channel_id: Optional[str] = typer.Option(
        None,
        "--slack-channel-id",
        help="Slack channel ID to post status updates to",
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
        force_release_type=force_release_type,
        override_state_name=override_state_name,
        slack_token=slack_token,
        slack_channel_id=slack_channel_id,
    )

    # Use context manager version with automatic lock management
    with initialize_tree_and_state(config, args) as (tree, _):
        asyncio.run(async_tick_tock(tree, cutoff=tree_cutoff))


@app.command()
def status(
    release_tag: str = typer.Argument(..., help="Release tag (e.g., 8.4-m01-int1)"),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file (default: config.yaml)"
    ),
    slack: bool = typer.Option(False, "--slack", help="Post status to Slack"),
    slack_channel_id: Optional[str] = typer.Option(
        None,
        "--slack-channel-id",
        help="Slack channel ID to post to (required if --slack is used)",
    ),
    slack_token: Optional[str] = typer.Option(
        None,
        "--slack-token",
        help="Slack bot token (if not provided, uses SLACK_BOT_TOKEN env var)",
    ),
) -> None:
    """Display release status in console and optionally post to Slack."""
    setup_logging()
    config_path = config_file or "config.yaml"
    config = load_config(config_path)

    # Create release args
    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=[],
    )

    with StateManager(
        storage=S3StateStorage(),
        config=config,
        args=args,
        read_only=True,
    ) as state_syncer:
        # Always print to console
        print_state_table(state_syncer.state)

        # Post to Slack if requested
        if slack:
            printer = init_slack_printer(slack_token, slack_channel_id)
            printer.update_message(state_syncer.state)


if __name__ == "__main__":
    app()
