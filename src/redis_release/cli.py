"""Redis OSS Release Automation CLI."""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

import typer
from openai import OpenAI
from py_trees.display import render_dot_tree, unicode_tree

from .bht.conversation_state import InboxMessage
from .bht.conversation_tree import (
    create_conversation_root_node,
    initialize_conversation_tree,
)
from .bht.tree import TreeInspector, async_tick_tock, initialize_tree_and_state
from .config import load_config
from .conversation_models import ConversationArgs, InboxMessage

# from .github_app_auth import GitHubAppAuth, load_private_key_from_file
from .github_client_async import GitHubClientAsync
from .logging_config import setup_logging
from .models import ReleaseArgs, ReleaseType, SlackArgs
from .state_display import print_state_table
from .state_manager import InMemoryStateStorage, S3StateStorage, StateManager
from .state_slack import init_slack_printer

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
    setup_logging()
    tree, state = initialize_conversation_tree(
        ConversationArgs(
            inbox=InboxMessage(message="test", context=[]), openai_api_key="dummy"
        )
    )
    render_dot_tree(tree.root)
    print(unicode_tree(tree.root))


@app.command()
def conversation(
    message: str = typer.Option(
        ..., "--message", "-m", help="Natural language release command"
    ),
    config: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file (default: config.yaml)"
    ),
    openai_api_key: Optional[str] = typer.Option(
        None,
        "--openai-api-key",
        help="OpenAI API key (if not provided, uses OPENAI_API_KEY env var)",
    ),
    tree_cutoff: int = typer.Option(
        5000, "--tree-cutoff", help="Max number of ticks to run the tree for"
    ),
) -> None:
    setup_logging()
    if not openai_api_key:
        openai_api_key = os.getenv("OPENAI_API_KEY")

    args = ConversationArgs(
        inbox=InboxMessage(message=message, context=[]),
        openai_api_key=openai_api_key,
        config_path=config,
    )
    tree, _ = initialize_conversation_tree(args)
    tree.tick()
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

    # Create release args
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

        # Post to Slack if requested
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


@app.command()
def test_github_app(
    github_app_id: str = typer.Option(..., "--github-app-id", help="GitHub App ID"),
    github_private_key_file: str = typer.Option(
        ..., "--github-private-key-file", help="Path to GitHub App private key file"
    ),
    repo: str = typer.Option(
        "redis/docker-library-redis",
        "--repo",
        help="Repository to test (default: redis/docker-library-redis)",
    ),
    workflow_file: str = typer.Option(
        "release_build_and_test.yml",
        "--workflow-file",
        help="Workflow file to dispatch (default: release_build_and_test.yml)",
    ),
    workflow_ref: str = typer.Option(
        "main", "--workflow-ref", help="Git ref to run workflow on (default: main)"
    ),
    workflow_inputs: Optional[str] = typer.Option(
        None,
        "--workflow-inputs",
        help='Workflow inputs as JSON string (e.g., \'{"key": "value"}\')',
    ),
) -> None:
    """[TEST] Test GitHub App authentication and workflow dispatch.

    This command tests GitHub App authentication by:
    1. Loading the private key from file
    2. Generating a JWT token
    3. Getting an installation token for the specified repository
    4. Dispatching a workflow using the installation token

    Example:
        redis-release test-github-app \\
            --github-app-id 123456 \\
            --github-private-key-file /path/to/private-key.pem \\
            --repo redis/docker-library-redis \\
            --workflow-file release_build_and_test.yml \\
            --workflow-ref main \\
            --workflow-inputs '{"release_tag": "8.4-m01-int1"}'
    """
    setup_logging()

    try:
        # Load private key
        logger.info(f"Loading private key from {github_private_key_file}")
        private_key = load_private_key_from_file(github_private_key_file)
        logger.info("[green]Private key loaded successfully[/green]")

        # Create GitHub App auth helper
        app_auth = GitHubAppAuth(app_id=github_app_id, private_key=private_key)

        # Get installation token
        logger.info(f"Getting installation token for repo: {repo}")

        async def get_token_and_dispatch() -> None:
            token = await app_auth.get_token_for_repo(repo)
            if not token:
                logger.error("[red]Failed to get installation token[/red]")
                raise typer.Exit(1)

            logger.info("[green]Successfully obtained installation token[/green]")
            logger.info(f"Token (first 20 chars): {token[:20]}...")

            # Parse workflow inputs
            inputs = {}
            if workflow_inputs:
                try:
                    inputs = json.loads(workflow_inputs)
                    logger.info(f"Workflow inputs: {inputs}")
                except json.JSONDecodeError as e:
                    logger.error(f"[red]Invalid JSON in workflow inputs:[/red] {e}")
                    raise typer.Exit(1)

            # Create GitHub client with the installation token
            github_client = GitHubClientAsync(token=token)

            # Dispatch workflow
            logger.info(
                f"Dispatching workflow {workflow_file} on {repo} at ref {workflow_ref}"
            )
            try:
                await github_client.trigger_workflow(
                    repo=repo,
                    workflow_file=workflow_file,
                    inputs=inputs,
                    ref=workflow_ref,
                )
                logger.info("[green]Workflow dispatched successfully![/green]")
                logger.info(
                    f"Check workflow runs at: https://github.com/{repo}/actions"
                )
            except Exception as e:
                logger.error(f"[red]Failed to dispatch workflow:[/red] {e}")
                raise typer.Exit(1)

        # Run async function
        asyncio.run(get_token_and_dispatch())

    except FileNotFoundError:
        logger.error(
            f"[red]Private key file not found:[/red] {github_private_key_file}"
        )
        raise typer.Exit(1)
    except Exception as e:
        logger.error(f"[red]Unexpected error:[/red] {e}", exc_info=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
