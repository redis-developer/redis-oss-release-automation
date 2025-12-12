import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay

from redis_release.models import SlackArgs

from .bht.conversation_tree import initialize_conversation_tree, run_conversation_tree
from .conversation_models import ConversationArgs

logger = logging.getLogger(__name__)


class ReleaseBot:
    """Async Slack bot that processes mentions via conversation tree.

    Bot conversation flow is unidirectional and stateless.

    ReleaseBot listens to mentions and pass them to conversation tree along with slack channel/thread params.

    While executing conversation tree may
        * Start a command which could eventually send message to the same
          channel/thread (independently of the bot socket process)
        * Put a reply message into the conversation state

    If reply message exists bot would send it back.

    The message could be a question came from the LLM. Then upon reply from user
    we start a new conversation tree but LLM receives the context - that is how
    feedback loop is achieved. In fact the state is in context (all thread
    messages)
    """

    def __init__(
        self,
        slack_bot_token: Optional[str] = None,
        slack_app_token: Optional[str] = None,
        reply_in_thread: bool = True,
        broadcast_to_channel: bool = False,
        authorized_users: Optional[List[str]] = None,
        openai_api_key: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> None:
        """Initialize the bot.

        Args:
            slack_bot_token: Slack bot token (xoxb-...). If None, uses SLACK_BOT_TOKEN env var
            slack_app_token: Slack app token (xapp-...). If None, uses SLACK_APP_TOKEN env var
            reply_in_thread: If True, reply in thread. If False, reply in main channel
            broadcast_to_channel: If True and reply_in_thread is True, also show in main channel
            authorized_users: List of user IDs authorized to run commands. If None, all users are authorized
            llm: OpenAI client for LLM-based command detection
        """
        self.reply_in_thread = reply_in_thread
        self.broadcast_to_channel = broadcast_to_channel
        self.authorized_users = authorized_users or []

        self.config_path = config_path

        # Get tokens from args or environment
        bot_token = slack_bot_token or os.environ.get("SLACK_BOT_TOKEN")
        app_token = slack_app_token or os.environ.get("SLACK_APP_TOKEN")
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")

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
            """Handle app mentions by processing through conversation tree."""
            try:
                text = event.get("text", "")
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

                # Check authorization
                if self.authorized_users and user not in self.authorized_users:
                    logger.warning(
                        f"Unauthorized attempt by user {user}. Authorized users: {self.authorized_users}"
                    )
                    await self._send_reply(
                        channel,
                        thread_ts,
                        f"<@{user}> Sorry, you are not authorized. Please contact an administrator.",
                    )
                    return

                # Get thread context if in a thread
                context = await self._get_thread_messages(channel, thread_ts)

                # Create and tick conversation tree
                args = ConversationArgs(
                    message=text,
                    context=context,
                    openai_api_key=self.openai_api_key,
                    config_path=self.config_path,
                    slack_args=SlackArgs(
                        bot_token=self.bot_token,
                        channel_id=channel,
                        thread_ts=thread_ts,
                        reply_broadcast=self.broadcast_to_channel,
                    ),
                )
                tree, state = initialize_conversation_tree(args)
                run_conversation_tree(tree)

                # Get reply from state
                reply = state.reply

                # Send reply
                if reply:
                    await self._send_reply(channel, thread_ts, reply)
                else:
                    await self._send_reply(
                        channel,
                        thread_ts,
                        f"<@{user}> I couldn't understand your request. Please try again.",
                    )

            except Exception as e:
                logger.error(f"Error handling app mention: {e}", exc_info=True)
                channel = event.get("channel")
                if channel:
                    await self._send_reply(
                        channel,
                        event.get("thread_ts", event.get("ts", "")),
                        f"Sorry, I encountered an error: {str(e)}",
                    )

    async def _get_thread_messages(self, channel: str, thread_ts: str) -> List[str]:
        """Get all messages from a thread.

        Args:
            channel: Slack channel ID
            thread_ts: Thread timestamp

        Returns:
            List of message texts from the thread
        """
        try:
            # Get thread messages using conversations.replies
            result = await self.app.client.conversations_replies(
                channel=channel, ts=thread_ts
            )

            messages = result.get("messages", [])
            # Extract text from messages, excluding the bot's own messages
            context = [
                msg.get("text", "")
                for msg in messages
                if msg.get("text") and not msg.get("bot_id")
            ]
            return context

        except Exception as e:
            logger.error(f"Error getting thread messages: {e}", exc_info=True)
            return []

    async def _send_reply(self, channel: str, thread_ts: str, text: str) -> None:
        """Send a reply message.

        Args:
            channel: Slack channel ID
            thread_ts: Thread timestamp to reply in
            text: Message text to send
        """
        if self.reply_in_thread:
            await self.app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
                reply_broadcast=self.broadcast_to_channel,
            )
        else:
            await self.app.client.chat_postMessage(
                channel=channel,
                text=text,
            )

    async def start(self) -> None:
        """Start the bot using Socket Mode."""
        logger.info("Starting Slack bot in Socket Mode...")
        handler = AsyncSocketModeHandler(self.app, self.app_token)
        await handler.start_async()


async def run_bot(
    slack_bot_token: Optional[str] = None,
    slack_app_token: Optional[str] = None,
    reply_in_thread: bool = True,
    broadcast_to_channel: bool = False,
    authorized_users: Optional[List[str]] = None,
    openai_api_key: Optional[str] = None,
    config_path: Optional[str] = None,
) -> None:
    """Run the Slack bot.

    Args:
        slack_bot_token: Slack bot token (xoxb-...). If None, uses SLACK_BOT_TOKEN env var
        slack_app_token: Slack app token (xapp-...). If None, uses SLACK_APP_TOKEN env var
        reply_in_thread: If True, reply in thread. If False, reply in main channel
        broadcast_to_channel: If True and reply_in_thread is True, also show in main channel
        authorized_users: List of user IDs authorized to run commands. If None, all users are authorized
        openai_api_key: OpenAI API key for LLM-based command detection. If None, uses OPENAI_API_KEY env var
    """

    # Create and start bot
    bot = ReleaseBot(
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        reply_in_thread=reply_in_thread,
        broadcast_to_channel=broadcast_to_channel,
        authorized_users=authorized_users,
        openai_api_key=openai_api_key,
        config_path=config_path,
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
