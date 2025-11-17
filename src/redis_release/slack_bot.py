"""Async Slack bot that listens for status requests and posts release status."""

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay

from redis_release.config import Config, load_config
from redis_release.models import ReleaseArgs
from redis_release.state_manager import S3StateStorage, StateManager
from redis_release.state_slack import SlackStatePrinter

logger = logging.getLogger(__name__)

# Regex pattern to match version tags like 8.4-m01, 7.2.5, 8.0-rc1, etc.
VERSION_TAG_PATTERN = re.compile(r"\b(\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9]+)?)\b")


class ReleaseStatusBot:
    """Async Slack bot that responds to status requests for releases."""

    def __init__(
        self,
        config: Config,
        slack_bot_token: Optional[str] = None,
        slack_app_token: Optional[str] = None,
        reply_in_thread: bool = True,
        broadcast_to_channel: bool = False,
    ):
        """Initialize the bot.

        Args:
            config: Release configuration
            slack_bot_token: Slack bot token (xoxb-...). If None, uses SLACK_BOT_TOKEN env var
            slack_app_token: Slack app token (xapp-...). If None, uses SLACK_APP_TOKEN env var
            reply_in_thread: If True, reply in thread. If False, reply in main channel
            broadcast_to_channel: If True and reply_in_thread is True, also show in main channel
        """
        self.config = config
        self.reply_in_thread = reply_in_thread
        self.broadcast_to_channel = broadcast_to_channel

        # Get tokens from args or environment
        bot_token = slack_bot_token or os.environ.get("SLACK_BOT_TOKEN")
        app_token = slack_app_token or os.environ.get("SLACK_APP_TOKEN")

        if not bot_token:
            raise ValueError(
                "Slack bot token not provided. Use slack_bot_token argument or set SLACK_BOT_TOKEN environment variable"
            )
        if not app_token:
            raise ValueError(
                "Slack app token not provided. Use slack_app_token argument or set SLACK_APP_TOKEN environment variable"
            )

        # Store validated tokens (guaranteed to be non-None)
        self.bot_token: str = bot_token
        self.app_token: str = app_token

        # Initialize async Slack app
        self.app = AsyncApp(token=self.bot_token)

        # Register event handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Slack event handlers."""

        @self.app.event("app_mention")
        async def handle_app_mention(  # pyright: ignore[reportUnusedFunction]
            event: Dict[str, Any], say: AsyncSay, logger: logging.Logger
        ) -> None:
            """Handle app mentions and check for status requests."""
            try:
                text = event.get("text", "").lower()
                channel = event.get("channel")
                user = event.get("user")
                ts = event.get("ts")
                thread_ts = event.get(
                    "thread_ts", ts
                )  # Use thread_ts if in thread, else use message ts

                # Validate required fields
                if not channel or not user or not thread_ts:
                    logger.error(
                        f"Missing required fields in event: channel={channel}, user={user}, thread_ts={thread_ts}"
                    )
                    return

                logger.info(
                    f"Received mention from user {user} in channel {channel}: {text}"
                )

                # Check if message contains "status"
                if "status" not in text:
                    logger.debug("Message doesn't contain 'status', ignoring")
                    return

                # Extract version tag from message
                tag = self._extract_version_tag(event.get("text", ""))

                if not tag:
                    # Reply in thread if configured
                    if self.reply_in_thread:
                        await self.app.client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=f"<@{user}> I couldn't find a version tag in your message. "
                            "Please mention me with 'status' and a version tag like `8.4-m01` or `7.2.5`.",
                        )
                    else:
                        await say(
                            f"<@{user}> I couldn't find a version tag in your message. "
                            "Please mention me with 'status' and a version tag like `8.4-m01` or `7.2.5`."
                        )
                    return

                logger.info(f"Processing status request for tag: {tag}")

                # Post status for the tag
                await self._post_status(tag, channel, user, thread_ts)

            except Exception as e:
                logger.error(f"Error handling app mention: {e}", exc_info=True)
                # Reply in thread if configured
                channel = event.get("channel")
                if self.reply_in_thread and channel:
                    await self.app.client.chat_postMessage(
                        channel=channel,
                        thread_ts=event.get("thread_ts", event.get("ts", "")),
                        text=f"Sorry, I encountered an error: {str(e)}",
                    )
                else:
                    await say(f"Sorry, I encountered an error: {str(e)}")

    def _extract_version_tag(self, text: str) -> Optional[str]:
        """Extract version tag from message text.

        Args:
            text: Message text

        Returns:
            Version tag if found, None otherwise
        """
        match = VERSION_TAG_PATTERN.search(text)
        if match:
            return match.group(1)
        return None

    async def _post_status(
        self, tag: str, channel: str, user: str, thread_ts: str
    ) -> None:
        """Load and post release status for a tag.

        Args:
            tag: Release tag
            channel: Slack channel ID
            user: User ID who requested the status
            thread_ts: Thread timestamp to reply in
        """
        try:
            # Create release args
            args = ReleaseArgs(
                release_tag=tag,
                force_rebuild=[],
            )

            # Load state from S3
            storage = S3StateStorage()

            # Use StateManager in read-only mode
            with StateManager(
                storage=storage,
                config=self.config,
                args=args,
                read_only=True,
            ) as state_syncer:
                state = state_syncer.state

                # Check if state exists (has any data beyond defaults)
                if not state.meta.last_started_at:
                    if self.reply_in_thread:
                        await self.app.client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=f"<@{user}> No release state found for tag `{tag}`. "
                            "This release may not have been started yet.",
                        )
                    else:
                        await self.app.client.chat_postMessage(
                            channel=channel,
                            text=f"<@{user}> No release state found for tag `{tag}`. "
                            "This release may not have been started yet.",
                        )
                    return

                # Get status blocks from SlackStatePrinter
                printer = SlackStatePrinter(self.bot_token, channel)
                blocks = printer._make_blocks(state)
                text = f"Release {state.meta.tag or 'N/A'} — Status"

                if self.reply_in_thread:
                    await self.app.client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=text,
                        blocks=blocks,
                        reply_broadcast=self.broadcast_to_channel,
                    )
                else:
                    await self.app.client.chat_postMessage(
                        channel=channel,
                        text=text,
                        blocks=blocks,
                    )

                logger.info(
                    f"Posted status for tag {tag} to channel {channel}"
                    + (f" in thread {thread_ts}" if self.reply_in_thread else "")
                )

        except Exception as e:
            logger.error(f"Error posting status for tag {tag}: {e}", exc_info=True)
            if self.reply_in_thread:
                await self.app.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"<@{user}> Failed to load status for tag `{tag}`: {str(e)}",
                )
            else:
                await self.app.client.chat_postMessage(
                    channel=channel,
                    text=f"<@{user}> Failed to load status for tag `{tag}`: {str(e)}",
                )

    async def start(self) -> None:
        """Start the bot using Socket Mode."""
        logger.info("Starting Slack bot in Socket Mode...")
        handler = AsyncSocketModeHandler(self.app, self.app_token)
        await handler.start_async()


async def run_bot(
    config_path: str = "config.yaml",
    slack_bot_token: Optional[str] = None,
    slack_app_token: Optional[str] = None,
    reply_in_thread: bool = True,
    broadcast_to_channel: bool = False,
) -> None:
    """Run the Slack bot.

    Args:
        config_path: Path to config file
        slack_bot_token: Slack bot token (xoxb-...). If None, uses SLACK_BOT_TOKEN env var
        slack_app_token: Slack app token (xapp-...). If None, uses SLACK_APP_TOKEN env var
        reply_in_thread: If True, reply in thread. If False, reply in main channel
        broadcast_to_channel: If True and reply_in_thread is True, also show in main channel
    """
    # Load config
    config = load_config(config_path)

    # Create and start bot
    bot = ReleaseStatusBot(
        config=config,
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        reply_in_thread=reply_in_thread,
        broadcast_to_channel=broadcast_to_channel,
    )

    await bot.start()


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run the bot
    asyncio.run(run_bot())
