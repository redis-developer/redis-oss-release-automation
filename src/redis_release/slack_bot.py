import asyncio
import logging
import os
import threading
from typing import Any, Callable, Coroutine, Dict, List, Optional

import janus
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay

from redis_release.bht.conversation_state import InboxMessage
from redis_release.models import SlackArgs

from .bht.conversation_tree import initialize_conversation_tree, run_conversation_tree
from .conversation_models import ConversationArgs

logger = logging.getLogger(__name__)


class ReleaseBot:
    """Async Slack bot that processes mentions via conversation tree.

    Bot conversation flow is unidirectional and stateless (there is no any direct conversation state kept in the bot).

    ReleaseBot listens to mentions and passes them to conversation tree along with slack channel/thread params and reply queue.

    For each inbox message listener task is created that listens to the reply queue and
    sends messages back to the user and exits when conversation tree thread is done.

    That way we keep conversation tree decoupled from Slack which in theory allow us to use it in CLI or web.

    While executing conversation tree may
        * Start a command which could eventually send message to the same
          channel/thread (independently of the bot)
        * Put a reply message(s) into the conversation state, which would be sent back via the queue

    The reply message could be a question came from the LLM. Then upon reply
    from user we start a new conversation tree but LLM receives the context -
    that is how feedback loop is achieved. In fact the state lives in context
    (all slack thread messages)

    Slack bot --Inbbox message--> New Conversation tree for each msg (thread) --> Release command (thread)
                                            |                                          |
                                            |                                          |
                                            v                                          v
    slack msg <-- listener <-- queue <--  reply                                   Slack messages (independent of the bot, same thread)
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

    def start_conversation(
        self,
        args: ConversationArgs,
    ) -> None:
        """Start a conversation tree and listener in a separate thread.

        Args:
            args: Conversation arguments
        """
        queue: janus.Queue[str] = janus.Queue()

        tree, state = initialize_conversation_tree(args, queue.sync_q)

        # Create thread for running the tree (but don't start yet)
        tree_thread = threading.Thread(
            target=run_conversation_tree,
            args=(tree, state, queue.sync_q),
            daemon=True,
        )

        assert args.slack_args
        assert args.slack_args.channel_id
        assert args.slack_args.thread_ts

        # Create and start queue listener as background task
        queue_listener = self.create_queue_listener(
            queue,
            args.slack_args.channel_id,
            args.slack_args.thread_ts,
            tree_thread,
        )
        asyncio.create_task(queue_listener())

        tree_thread.start()

    def _register_handlers(self) -> None:
        """Register Slack event handlers."""

        async def process_message(
            event: Dict[str, Any], logger: logging.Logger, is_mention: bool = False
        ) -> None:
            """Common message processing logic for both mentions and thread replies.

            Args:
                event: Slack event data
                logger: Logger instance
                is_mention: Whether this is an explicit mention (True) or thread reply (False)
            """
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

                # Type assertions after validation
                assert isinstance(channel, str)
                assert isinstance(user, str)
                assert isinstance(thread_ts, str)

                logger.info(
                    f"Received {'mention' if is_mention else 'thread message'} from user {user} in channel {channel}: {text}"
                )

                # Get slack thread messages
                context = await self._get_thread_messages(channel, thread_ts)

                inbox_message = InboxMessage(
                    message=text, user=user, context=context or []
                )
                args = ConversationArgs(
                    inbox=inbox_message,
                    config_path=self.config_path,
                    slack_args=SlackArgs(
                        bot_token=self.bot_token,
                        channel_id=channel,
                        thread_ts=thread_ts,
                        reply_broadcast=self.broadcast_to_channel,
                    ),
                    openai_api_key=self.openai_api_key,
                    authorized_users=self.authorized_users,
                )

                self.start_conversation(args)

            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)
                channel = event.get("channel")
                if channel:
                    await self._send_reply(
                        channel,
                        event.get("thread_ts", event.get("ts", "")),
                        f"Sorry, I encountered an error: {str(e)}",
                    )

        @self.app.event("app_mention")
        async def handle_app_mention(  # pyright: ignore[reportUnusedFunction]
            event: Dict[str, Any], say: AsyncSay, logger: logging.Logger
        ) -> None:
            """Handle app mentions by processing through conversation tree."""
            await process_message(event, logger, is_mention=True)

        @self.app.event("message")
        async def handle_message(  # pyright: ignore[reportUnusedFunction]
            event: Dict[str, Any], logger: logging.Logger
        ) -> None:
            """Handle messages in threads where bot is participating."""
            logger.debug(f"Received message event: {event}")

            # Ignore messages that are not in threads
            if "thread_ts" not in event:
                logger.debug("Ignoring non-thread message")
                return

            # Ignore bot's own messages
            if event.get("bot_id"):
                logger.debug("Ignoring bot's own message")
                return

            # Ignore subtypes (like message_changed, message_deleted, etc.)
            if event.get("subtype"):
                logger.debug(f"Ignoring message with subtype: {event.get('subtype')}")
                return

            channel = event.get("channel")
            thread_ts = event.get("thread_ts")

            if not channel or not thread_ts:
                logger.debug("Missing channel or thread_ts")
                return

            logger.debug(f"Checking if bot is participating in thread {thread_ts}")

            # Check if bot has participated in this thread
            is_participating = await self._is_bot_in_thread(channel, thread_ts)

            logger.debug(f"Bot participating in thread: {is_participating}")

            if is_participating:
                logger.info(
                    f"Processing thread message in channel {channel}, thread {thread_ts}"
                )
                await process_message(event, logger, is_mention=False)
            else:
                logger.debug("Bot not participating in this thread, ignoring message")

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

    async def _is_bot_in_thread(self, channel: str, thread_ts: str) -> bool:
        """Check if the bot has participated in a thread.

        Args:
            channel: Slack channel ID
            thread_ts: Thread timestamp

        Returns:
            True if bot has sent messages in the thread, False otherwise
        """
        try:
            # Get bot's user ID
            auth_result = await self.app.client.auth_test()
            bot_user_id = auth_result.get("user_id")

            if not bot_user_id:
                logger.warning("Could not get bot user ID")
                return False

            logger.debug(f"Bot user ID: {bot_user_id}")

            # Get thread messages
            result = await self.app.client.conversations_replies(
                channel=channel, ts=thread_ts
            )

            messages = result.get("messages", [])
            logger.debug(f"Found {len(messages)} messages in thread {thread_ts}")

            # Check if any message is from the bot
            for msg in messages:
                msg_user = msg.get("user")
                msg_bot_id = msg.get("bot_id")
                logger.debug(f"Message from user={msg_user}, bot_id={msg_bot_id}")

                if msg_user == bot_user_id or msg_bot_id:
                    logger.debug(f"Found bot message in thread")
                    return True

            logger.debug(f"No bot messages found in thread")
            return False

        except Exception as e:
            logger.error(
                f"Error checking bot participation in thread: {e}", exc_info=True
            )
            return False

    def create_queue_listener(
        self,
        queue: janus.Queue[str],
        channel: str,
        thread_ts: str,
        tree_thread: threading.Thread,
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        """Create a queue listener function for the given async queue and Slack args.

        Args:
            queue: Janus queue to listen to
            channel: Slack channel ID
            thread_ts: Thread timestamp to reply in
            tree_thread: The thread running the conversation tree

        Returns:
            An async function that listens to the queue and sends messages to Slack
        """

        async_q = queue.async_q

        async def queue_listener() -> None:
            """Listen to async queue and send messages to Slack thread."""
            try:
                while True:
                    try:
                        # Wait for message from queue with timeout
                        message = await asyncio.wait_for(async_q.get(), timeout=1)
                        logger.debug(f"Received message from queue")

                        # Send message to Slack thread
                        try:
                            await self._send_reply(channel, thread_ts, message)
                        except Exception as e:
                            logger.error(
                                f"Error sending message to Slack: {e}",
                                exc_info=True,
                            )
                    except asyncio.TimeoutError:
                        # Check if tree thread is done
                        if not tree_thread.is_alive():
                            logger.debug(
                                "Tree thread completed, exiting queue listener"
                            )
                            queue.close()
                            break
                    except janus.AsyncQueueShutDown:
                        logger.debug("Queue listener shutting down")
                        break
                    except asyncio.CancelledError:
                        logger.debug("Queue listener cancelled")
                        break
            finally:
                if not queue.closed:
                    queue.close()
                await queue.wait_closed()
                logger.debug("Queue listener exiting")

        return queue_listener

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
