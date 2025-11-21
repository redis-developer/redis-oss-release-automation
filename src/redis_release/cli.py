"""Redis OSS Release Automation CLI."""

import asyncio
import logging
from typing import Dict, List, Optional

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


def parse_force_release_type(
    force_release_type_list: Optional[List[str]],
) -> Dict[str, ReleaseType]:
    """Parse force_release_type arguments from 'package_name:release_type' format.

    Args:
        force_release_type_list: List of strings in format 'package_name:release_type'

    Returns:
        Dictionary mapping package names to ReleaseType

    Raises:
        typer.BadParameter: If format is invalid or release type is unknown
    """
    if not force_release_type_list:
        return {}

    result = {}
    for item in force_release_type_list:
        if ":" not in item:
            raise typer.BadParameter(
                f"Invalid format '{item}'. Expected 'package_name:release_type' (e.g., 'docker:internal')"
            )

        package_name, release_type_str = item.split(":", 1)
        package_name = package_name.strip()
        release_type_str = release_type_str.strip().lower()

        try:
            release_type = ReleaseType(release_type_str)
        except ValueError:
            valid_types = ", ".join([rt.value for rt in ReleaseType])
            raise typer.BadParameter(
                f"Invalid release type '{release_type_str}'. Valid types: {valid_types}"
            )

        result[package_name] = release_type

    return result


@app.command()
def release_print(
    release_tag: str = typer.Argument(..., help="Release tag (e.g., 8.4-m01-int1)"),
    package_type: Optional[str] = typer.Option(
        None,
        "--package-type",
        "-p",
        help="Package type to use for creating the tree (default: docker)",
    ),
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
        inspector = TreeInspector(release_tag=release_tag, package_type=package_type)

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
    force_release_type: Optional[List[str]] = typer.Option(
        None,
        "--force-release-type",
        help="Force release type per package in format 'package_name:release_type' (e.g., 'docker:internal' or 'all:public'). Can be specified multiple times.",
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
        force_release_type=parse_force_release_type(force_release_type),
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


@app.command()
def slack_bot(
    config_file: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file (default: config.yaml)"
    ),
    slack_bot_token: Optional[str] = typer.Option(
        None,
        "--slack-bot-token",
        help="Slack bot token (xoxb-...). If not provided, uses SLACK_BOT_TOKEN env var",
    ),
    slack_app_token: Optional[str] = typer.Option(
        None,
        "--slack-app-token",
        help="Slack app token (xapp-...). If not provided, uses SLACK_APP_TOKEN env var",
    ),
    reply_in_thread: bool = typer.Option(
        True,
        "--reply-in-thread/--no-reply-in-thread",
        help="Reply in thread instead of main channel",
    ),
    broadcast_to_channel: bool = typer.Option(
        False,
        "--broadcast/--no-broadcast",
        help="When replying in thread, also show in main channel",
    ),
    authorized_users: Optional[List[str]] = typer.Option(
        None,
        "--authorized-user",
        help="User ID authorized to run releases (can be specified multiple times). If not specified, all users are authorized",
    ),
) -> None:
    """Run Slack bot that listens for status requests.

    The bot listens for mentions containing 'status' and a version tag (e.g., '8.4-m01'),
    and responds by posting the release status to the channel.

    By default, replies are posted in threads to keep channels clean. Use --no-reply-in-thread
    to post directly in the channel. Use --broadcast to show thread replies in the main channel.

    Only users specified with --authorized-user can run releases. Status command is available to all users.
    You can also include the word 'broadcast' in the release message to broadcast updates to the main channel.

    Requires Socket Mode to be enabled in your Slack app configuration.
    """
    from redis_release.slack_bot import run_bot

    setup_logging()
    config_path = config_file or "config.yaml"

    logger.info("Starting Slack bot...")
    asyncio.run(
        run_bot(
            config_path,
            slack_bot_token,
            slack_app_token,
            reply_in_thread,
            broadcast_to_channel,
            authorized_users,
        )
    )


if __name__ == "__main__":
    app()
