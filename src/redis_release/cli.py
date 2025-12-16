"""Redis OSS Release Automation CLI."""

import asyncio
import logging
import os
from typing import List, Optional

import typer
from openai import OpenAI
from py_trees.display import render_dot_tree, unicode_tree

from redis_release.cli_util import parse_force_release_type, parse_module_versions

from .bht.conversation_state import InboxMessage
from .bht.conversation_tree import initialize_conversation_tree
from .bht.tree import TreeInspector, async_tick_tock, initialize_tree_and_state
from .config import load_config
from .conversation_models import ConversationArgs, InboxMessage
from .logging_config import setup_logging
from .models import RedisModule, ReleaseArgs, SlackArgs
from .state_display import print_state_table
from .state_manager import InMemoryStateStorage, S3StateStorage, StateManager
from .state_slack import init_slack_printer

app = typer.Typer(
    name="redis-release",
    help="Redis OSS Release Automation CLI",
    add_completion=False,
)

logger = logging.getLogger(__name__)


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
            logger.error(f"[red]Error: {e}[/red]", exc_info=True)
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
def conversation_print() -> None:
    """Print and render (using graphviz) the conversation behaviour tree."""
    setup_logging()
    tree, _ = initialize_conversation_tree(
        ConversationArgs(
            inbox=InboxMessage(message="test", context=[]), openai_api_key="dummy"
        )
    )
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
    module_versions: Optional[List[str]] = typer.Option(
        None,
        "--module-version",
        help="Specific module version to use (e.g., 'redisjson:2.4.0'). Can be specified multiple times.",
    ),
    tree_cutoff: int = typer.Option(
        5000, "--tree-cutoff", "-m", help="Max number of ticks to run the tree for"
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
        module_versions=parse_module_versions(module_versions),
        slack_args=SlackArgs(
            bot_token=slack_token,
            channel_id=slack_channel_id,
        ),
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
    override_state_name: Optional[str] = typer.Option(
        None,
        "--override-state-name",
        help="Custom state name to use instead of release tag, to be able to make test runs without affecting production state",
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

    args = ReleaseArgs(
        release_tag=release_tag,
        force_rebuild=[],
        override_state_name=override_state_name,
    )

    with StateManager(
        storage=S3StateStorage(),
        config=config,
        args=args,
        read_only=True,
    ) as state_syncer:
        # Always print to console
        print_state_table(state_syncer.state)

        if slack:
            printer = init_slack_printer(slack_token, slack_channel_id)
            printer.update_message(state_syncer.state)


@app.command()
def slack_bot(
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
        help="User ID authorized to run commands (can be specified multiple times). If not specified, all users are authorized",
    ),
    openai_api_key: Optional[str] = typer.Option(
        None,
        "--openai-api-key",
        help="OpenAI API key for LLM-based command detection. If not provided, uses OPENAI_API_KEY env var",
    ),
    config: str = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (default: config.yaml)",
    ),
) -> None:
    """Run Slack bot that processes mentions via conversation tree.

    The bot listens for mentions and processes them through a conversation tree
    to detect and execute commands.

    Requires Socket Mode to be enabled in your Slack app configuration.
    """
    from redis_release.slack_bot import run_bot

    setup_logging()

    logger.info("Starting Slack bot...")
    asyncio.run(
        run_bot(
            slack_bot_token=slack_bot_token,
            slack_app_token=slack_app_token,
            reply_in_thread=reply_in_thread,
            broadcast_to_channel=broadcast_to_channel,
            authorized_users=authorized_users,
            openai_api_key=openai_api_key,
            config_path=config,
        )
    )


if __name__ == "__main__":
    app()
