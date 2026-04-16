"""Redis OSS Release Automation CLI."""

import asyncio
import logging
import os
from typing import List, Optional

import typer
from openai import OpenAI
from py_trees.display import render_dot_tree, unicode_tree

from redis_release.bht.tree import run_tree_with_shutdown
from redis_release.cli_util import (
    parse_force_release_type,
    parse_module_versions,
    parse_slack_format,
)

from .bht.conversation_state import InboxMessage
from .bht.conversation_tree import initialize_conversation_tree
from .bht.tree import TreeInspector, initialize_tree_and_state
from .config import load_config
from .conversation_models import ConversationArgs, InboxMessage
from .logging_config import setup_logging
from .models import ReleaseArgs, SlackArgs
from .state_console import print_state_table
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
    custom_build: bool = typer.Option(
        False,
        "--custom-build",
        help="Enforce custom build mode, this will interpret release tag as a git ref (branch or tag) and use only packages supporting custom builds",
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
        custom_build=custom_build,
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
        ConversationArgs(inbox=InboxMessage(message="test"), openai_api_key="dummy")
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
    custom_build: bool = typer.Option(
        False,
        "--custom-build",
        help="Enforce custom build mode, this will interpret release tag as a git ref (branch or tag) and use only packages supporting custom builds",
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
    slack_thread_ts: Optional[str] = typer.Option(
        None,
        "--slack-thread-ts",
        help="Slack thread timestamp to post status updates to",
    ),
    slack_format: Optional[str] = typer.Option(
        None,
        "--slack-format",
        help="Slack message format to use. Available: default, compact",
    ),
    log_file: Optional[str] = typer.Option(
        None,
        "--log-file",
        help="Path to log file (if not provided, uses LOG_FILE env var)",
    ),
    log_file_level: Optional[str] = typer.Option(
        None,
        "--log-file-level",
        help="Log level for file output (default: debug). Supports: debug, info, warning, error, critical",
    ),
) -> None:
    """Run release using behaviour tree implementation."""
    setup_logging(log_file=log_file, log_file_level=log_file_level)
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
        custom_build=custom_build,
        slack_args=SlackArgs(
            bot_token=slack_token,
            channel_id=slack_channel_id,
            thread_ts=slack_thread_ts,
            format=parse_slack_format(slack_format),
        ),
    )

    # Use context manager version with automatic lock management
    with initialize_tree_and_state(config, args) as (tree, _):
        asyncio.run(run_tree_with_shutdown(tree, cutoff=tree_cutoff))


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
    slack_format: Optional[str] = typer.Option(
        None,
        "--slack-format",
        help="Slack message format to use. Available: default, compact",
    ),
    log_file: Optional[str] = typer.Option(
        None,
        "--log-file",
        help="Path to log file (if not provided, uses LOG_FILE env var)",
    ),
    log_file_level: Optional[str] = typer.Option(
        None,
        "--log-file-level",
        help="Log level for file output (default: debug). Supports: debug, info, warning, error, critical",
    ),
) -> None:
    """Display release status in console and optionally post to Slack."""
    setup_logging(log_file=log_file, log_file_level=log_file_level)
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
            printer = init_slack_printer(
                slack_token,
                slack_channel_id,
                slack_format=parse_slack_format(slack_format),
            )
            blocks = printer.make_blocks(state_syncer.state)
            printer.update_message(blocks)
            printer.stop()


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
    ignore_channels: Optional[List[str]] = typer.Option(
        None,
        "--ignore-channel",
        help="Channel ID to ignore messages from (can be specified multiple times)",
    ),
    only_channels: Optional[List[str]] = typer.Option(
        None,
        "--only-channel",
        help="Only process messages from these channel IDs (can be specified multiple times)",
    ),
    slack_format: str = typer.Option(
        "default",
        "--slack-format",
        help="Slack message format: 'default' or 'compact'",
    ),
    log_file: Optional[str] = typer.Option(
        None,
        "--log-file",
        help="Path to log file (if not provided, uses LOG_FILE env var)",
    ),
    log_file_level: Optional[str] = typer.Option(
        None,
        "--log-file-level",
        help="Log level for file output (default: debug). Supports: debug, info, warning, error, critical",
    ),
) -> None:
    """Run Slack bot that processes mentions via conversation tree.

    The bot listens for mentions and processes them through a conversation tree
    to detect and execute commands.

    Requires Socket Mode to be enabled in your Slack app configuration.
    """
    from redis_release.slack_bot import run_bot

    setup_logging(log_file=log_file, log_file_level=log_file_level)

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
            ignore_channels=ignore_channels,
            only_channels=only_channels,
            slack_format=parse_slack_format(slack_format),
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
        import json

        from .github_app_auth import GitHubAppAuth, load_private_key_from_file
        from .github_client_async import GitHubClientAsync

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
