import asyncio
import logging
import os
import random
import signal
import threading
from pprint import pformat
from typing import Any, Callable, Coroutine, Dict, List, Optional

import janus
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay

from redis_release.models import SlackArgs, SlackFormat
from redis_release.slack_emojis import FALLBACK_REACTION_EMOJI, STANDARD_EMOJIS

from .bht.conversation_tree import initialize_conversation_tree, run_conversation_tree
from .concurrency import ConcurrencyManager
from .conversation_models import (
    IGNORE_THREAD_MESSAGE,
    BotQueueItem,
    BotReaction,
    BotReply,
    ConversationArgs,
    InboxMessage,
)

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
        ignore_channels: Optional[List[str]] = None,
        only_channels: Optional[List[str]] = None,
        slack_format: SlackFormat = SlackFormat.DEFAULT,
    ) -> None:
        """Initialize the bot.

        Args:
            slack_bot_token: Slack bot token (xoxb-...). If None, uses SLACK_BOT_TOKEN env var
            slack_app_token: Slack app token (xapp-...). If None, uses SLACK_APP_TOKEN env var
            reply_in_thread: If True, reply in thread. If False, reply in main channel
            broadcast_to_channel: If True and reply_in_thread is True, also show in main channel
            authorized_users: List of user IDs authorized to run commands. If None, all users are authorized
            openai_api_key: OpenAI API key for LLM-based command detection
            ignore_channels: List of channel IDs to ignore messages from
            only_channels: List of channel IDs to only process messages from
            slack_format: Slack message format (DEFAULT or COMPACT)
        """
        self.reply_in_thread = reply_in_thread
        self.broadcast_to_channel = broadcast_to_channel
        self.authorized_users = authorized_users or []
        self.ignore_channels = ignore_channels or []
        self.only_channels = only_channels or []
        self.slack_format = slack_format

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

        # Bot identity (populated on start via auth.test)
        self.bot_user_id: Optional[str] = None
        self.bot_id: Optional[str] = None

        # Workspace emojis (populated on start)
        self.emojis: Dict[str, str] = {}

        # Track handled messages to avoid duplicate processing
        self.handled_messages_ts: set[str] = set()

        # Concurrency manager for graceful shutdown
        self._concurrency = ConcurrencyManager()

        # Socket mode handler (set during start)
        self._handler: Optional[AsyncSocketModeHandler] = None

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
        if self._concurrency.is_shutting_down:
            logger.warning("Bot is shutting down, ignoring new conversation")
            return

        queue: janus.Queue[BotQueueItem] = janus.Queue()

        tree, state = initialize_conversation_tree(
            args, queue.sync_q, concurrency_manager=self._concurrency
        )
        logger.debug("Init State: " + pformat(state))

        # Create thread for running the tree (but don't start yet)
        tree_thread = threading.Thread(
            target=run_conversation_tree,
            args=(tree, state, queue.sync_q),
            daemon=True,
        )

        assert args.slack_args
        assert args.slack_args.channel_id
        assert args.slack_args.thread_ts
        assert args.inbox

        # Get the inbox message timestamp for reactions
        inbox_ts = args.inbox.slack_ts

        # Create and start queue listener as background task
        queue_listener = self.create_queue_listener(
            queue,
            args.slack_args.channel_id,
            args.slack_args.thread_ts,
            tree_thread,
            inbox_ts,
        )
        task = asyncio.create_task(queue_listener())

        # Track active resources for graceful shutdown
        self._concurrency.register_task(task)
        self._concurrency.register_thread(tree_thread)
        self._concurrency.register_queue(queue)

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
                assert isinstance(ts, str)

                # Check for duplicate messages (mentions in threads trigger both events)
                if self.check_message_handled(ts):
                    logger.info(f"Skipping already handled message: {ts}")
                    return

                # Channel filtering
                if self.only_channels and channel not in self.only_channels:
                    logger.debug(
                        f"Ignoring message from channel {channel}: not in only_channels list"
                    )
                    return

                if self.ignore_channels and channel in self.ignore_channels:
                    logger.debug(
                        f"Ignoring message from channel {channel}: in ignore_channels list"
                    )
                    return

                logger.info(
                    f"Received {'mention' if is_mention else 'thread message'} from user {user} in channel {channel}, message {ts}: {text}"
                )

                # Get slack thread messages (excluding the current message)
                all_messages = await self._get_thread_messages(channel, thread_ts)
                # Filter out the current message from context to avoid duplication
                context = [msg for msg in all_messages if msg.slack_ts != ts]

                # Extract text from the event (including blocks)
                inbox_text = self._extract_text_from_message(event)
                inbox_message = InboxMessage(
                    message=inbox_text, user=user, slack_ts=ts, is_mention=is_mention
                )
                args = ConversationArgs(
                    inbox=inbox_message,
                    context=context,
                    config_path=self.config_path,
                    slack_args=SlackArgs(
                        bot_token=self.bot_token,
                        channel_id=channel,
                        thread_ts=thread_ts,
                        reply_broadcast=self.broadcast_to_channel,
                        format=self.slack_format,
                    ),
                    openai_api_key=self.openai_api_key,
                    authorized_users=self.authorized_users,
                    emojis=self._limit_emojis(),
                    slack_format_is_available=True,
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
                logger.warning("Missing channel or thread_ts")
                return

            logger.debug(f"Checking if bot is participating in thread {thread_ts}")

            # Check if bot has participated in this thread
            is_participating = await self.is_bot_participating(channel, thread_ts)

            logger.debug(f"Bot participating in thread: {is_participating}")

            if is_participating:
                logger.info(
                    f"Processing thread message in channel {channel}, thread {thread_ts}, user: {event.get('user')}, message {event.get('ts')}"
                )
                await process_message(event, logger, is_mention=False)
            else:
                logger.info(
                    f"Skipping message in {channel}, thread {thread_ts}, user: {event.get('user')}, message {event.get('ts')}: {event.get('text')}"
                )

    def _extract_rich_text_element(self, element: Dict[str, Any]) -> str:
        """Extract text from a rich_text element (recursive).

        Args:
            element: A rich_text element dict

        Returns:
            Extracted text string
        """
        element_type = element.get("type", "")

        # Direct text elements
        if element_type == "text":
            return str(element.get("text", ""))
        elif element_type == "link":
            # Links may have text or just URL
            return str(element.get("text", element.get("url", "")))
        elif element_type == "emoji":
            return f":{element.get('name', '')}:"
        elif element_type == "user":
            return f"<@{element.get('user_id', '')}>"
        elif element_type == "channel":
            return f"<#{element.get('channel_id', '')}>"
        elif element_type == "usergroup":
            return f"<!subteam^{element.get('usergroup_id', '')}>"
        elif element_type == "broadcast":
            return f"@{element.get('range', 'here')}"

        # Container elements with nested elements
        elif element_type in (
            "rich_text_section",
            "rich_text_quote",
        ):
            parts = []
            for sub_element in element.get("elements", []):
                parts.append(self._extract_rich_text_element(sub_element))
            return "".join(parts)
        elif element_type == "rich_text_preformatted":
            # Preformatted text should be wrapped in backticks to preserve code block formatting
            parts = []
            for sub_element in element.get("elements", []):
                parts.append(self._extract_rich_text_element(sub_element))
            content = "".join(parts)
            return f"```\n{content}```"
        elif element_type == "rich_text_list":
            parts = []
            style = element.get("style", "bullet")
            for i, item in enumerate(element.get("elements", [])):
                prefix = f"{i + 1}. " if style == "ordered" else "• "
                parts.append(prefix + self._extract_rich_text_element(item))
            return "\n".join(parts)

        return ""

    def _extract_block_text(self, block: Dict[str, Any]) -> List[str]:
        """Extract text from a single Slack block.

        Args:
            block: A Slack block dict

        Returns:
            List of text strings extracted from the block
        """
        block_type = block.get("type", "")
        texts: List[str] = []

        if block_type == "rich_text":
            # Rich text blocks have nested elements
            for element in block.get("elements", []):
                text = self._extract_rich_text_element(element)
                if text:
                    texts.append(text)

        elif block_type == "section":
            # Section blocks have text field and optional fields
            text_obj = block.get("text", {})
            if text_obj and text_obj.get("text"):
                texts.append(text_obj.get("text", ""))
            # Also check fields array
            for field in block.get("fields", []):
                if field.get("text"):
                    texts.append(field.get("text", ""))

        elif block_type == "header":
            text_obj = block.get("text", {})
            if text_obj and text_obj.get("text"):
                texts.append(text_obj.get("text", ""))

        elif block_type == "context":
            # Context blocks have elements array
            for element in block.get("elements", []):
                if (
                    element.get("type") == "mrkdwn"
                    or element.get("type") == "plain_text"
                ):
                    if element.get("text"):
                        texts.append(element.get("text", ""))

        elif block_type == "divider":
            texts.append("---")

        return texts

    def _extract_text_from_message(self, message: Dict[str, Any]) -> str:
        """Extract all text content from a Slack message with blocks.

        This handles messages with rich_text, section, header, context blocks
        and falls back to the plain text field if no blocks are present.

        Args:
            message: Slack message dict

        Returns:
            Extracted text content as a single string
        """
        blocks = message.get("blocks", [])

        if not blocks:
            # No blocks, use plain text fallback
            return str(message.get("text", ""))

        extracted_parts: List[str] = []
        for block in blocks:
            extracted_parts.extend(self._extract_block_text(block))

        if extracted_parts:
            return "\n".join(extracted_parts)

        # Fallback to text field if block extraction yielded nothing
        return str(message.get("text", ""))

    async def _get_thread_messages(
        self, channel: str, thread_ts: str
    ) -> List[InboxMessage]:
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

            messages: List[Dict[str, Any]] = result.get("messages", [])
            logger.debug(f"Msgs in thread " + pformat(messages))
            # Extract text from messages (including blocks)
            context: List[InboxMessage] = [
                InboxMessage(
                    message=self._extract_text_from_message(msg),
                    user=msg.get("user"),
                    is_from_bot=msg.get("bot_id") is not None,
                    slack_ts=msg.get("ts"),
                )
                for msg in messages
            ]
            logger.debug(f"Context: " + pformat(context))
            return context

        except Exception as e:
            logger.error(f"Error getting thread messages: {e}", exc_info=True)
            return []

    async def is_bot_participating(self, channel: str, thread_ts: str) -> bool:
        """Check if the bot has participated in a thread.

        Args:
            channel: Slack channel ID
            thread_ts: Thread timestamp

        Returns:
            True if bot has sent messages or been mentioned in the thread, False otherwise
        """
        try:
            # Use prefetched bot user ID
            if not self.bot_user_id:
                logger.warning("Bot user ID not available")
                return False

            # Get thread messages
            result = await self.app.client.conversations_replies(
                channel=channel, ts=thread_ts
            )

            messages: List[Dict[str, Any]] = result.get("messages", [])
            logger.debug(f"Found {len(messages)} messages in thread {thread_ts}")

            # Bot mention pattern: <@USER_ID>
            bot_mention = f"<@{self.bot_user_id}>"

            # Check if any message is from the bot or mentions the bot
            is_participating = False
            for msg in messages:
                msg_user = msg.get("user")
                msg_bot_id = msg.get("bot_id")
                msg_text = msg.get("text", "")

                logger.debug(f"Message from user={msg_user}, bot_id={msg_bot_id}")

                # Check if message is from the bot
                if msg_user == self.bot_user_id or msg_bot_id:
                    logger.debug(f"Found bot message in thread")
                    if IGNORE_THREAD_MESSAGE in msg_text:
                        logger.info(f"Found ignore marker message in thread")
                        is_participating = False
                        break

                # Check if message mentions the bot
                if bot_mention in msg_text:
                    logger.debug(f"Found bot mention in thread")
                    is_participating = True

            logger.debug(f"No bot messages or mentions found in thread")
            return is_participating

        except Exception as e:
            logger.error(
                f"Error checking bot participation in thread: {e}", exc_info=True
            )
            return False

    def create_queue_listener(
        self,
        queue: janus.Queue[BotQueueItem],
        channel: str,
        thread_ts: str,
        tree_thread: threading.Thread,
        inbox_ts: Optional[str] = None,
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        """Create a queue listener function for the given async queue and Slack args.

        Args:
            queue: Janus queue to listen to
            channel: Slack channel ID
            thread_ts: Thread timestamp to reply in
            tree_thread: The thread running the conversation tree
            inbox_ts: Timestamp of the inbox message (for reactions)

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
                        item = await asyncio.wait_for(async_q.get(), timeout=1)
                        logger.debug(f"Received item from queue: {type(item).__name__}")

                        # Handle the queue item based on its type
                        try:
                            if isinstance(item, BotReply):
                                await self._send_reply(channel, thread_ts, item.text)
                            elif isinstance(item, BotReaction):
                                # Use the message_ts from the reaction, or fall back to inbox_ts
                                reaction_ts = item.message_ts or inbox_ts
                                if reaction_ts:
                                    await self._add_reaction(
                                        channel, reaction_ts, item.emoji
                                    )
                                else:
                                    logger.warning(
                                        f"Cannot add reaction: no message timestamp available"
                                    )
                        except Exception as e:
                            logger.error(
                                f"Error sending to Slack: {e}",
                                exc_info=True,
                            )
                    except asyncio.TimeoutError:
                        # Check if tree thread is done
                        if not tree_thread.is_alive():
                            logger.debug(
                                "Tree thread completed, exiting queue listener"
                            )
                            self._concurrency.unregister_thread(tree_thread)
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
                # Unregister the tree thread from concurrency manager if it's done
                if not tree_thread.is_alive():
                    self._concurrency.unregister_thread(tree_thread)
                logger.debug("Queue listener exiting")

        return queue_listener

    async def _add_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        """Add a reaction to a message.

        Args:
            channel: Slack channel ID
            timestamp: Message timestamp to react to
            emoji: Emoji name (without colons)
        """
        # Use fallback emoji if the provided emoji doesn't exist in workspace or standard emojis
        if emoji not in self.emojis and emoji not in STANDARD_EMOJIS:
            logger.warning(
                f"Emoji '{emoji}' not found in workspace or standard emojis, using fallback '{FALLBACK_REACTION_EMOJI}'"
            )
            emoji = FALLBACK_REACTION_EMOJI

        await self.app.client.reactions_add(
            channel=channel,
            timestamp=timestamp,
            name=emoji,
        )

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

    async def _fetch_emojis(self) -> None:
        """Fetch workspace emojis and store them in self.emojis."""
        try:
            result = await self.app.client.emoji_list()
            if result.get("ok"):
                self.emojis = result.get("emoji", {})
                logger.info(f"Fetched {len(self.emojis)} workspace emojis")
                # logger.debug(f"Emojis: " + pformat(self.emojis))
            else:
                logger.warning(f"Failed to fetch emojis: {result.get('error')}")
        except Exception as e:
            logger.error(f"Error fetching emojis: {e}", exc_info=True)

    async def _fetch_bot_info(self) -> None:
        """Fetch bot identity info (user_id, bot_id) via auth.test API."""
        try:
            result = await self.app.client.auth_test()
            if result.get("ok"):
                self.bot_user_id = result.get("user_id")
                self.bot_id = result.get("bot_id")
                logger.info(
                    f"Bot identity: user_id={self.bot_user_id}, bot_id={self.bot_id}"
                )
            else:
                logger.warning(f"Failed to fetch bot info: {result.get('error')}")
        except Exception as e:
            logger.error(f"Error fetching bot info: {e}", exc_info=True)

    def _limit_emojis(self) -> List[str]:
        """Shuffle and return 10% of available emojis.

        Returns:
            List of emoji names (shuffled, 10% of total, minimum 1)
        """
        keys = list(self.emojis.keys())
        random.shuffle(keys)
        count = max(1, len(keys) // 10)
        return keys[:count]

    def check_message_handled(self, message_ts: str) -> bool:
        """Check if a message has already been handled.

        If the message was already handled, returns True.
        If not, adds it to the set and returns False.
        Clears the set before adding if it exceeds 100 entries.

        Args:
            message_ts: The message timestamp to check

        Returns:
            True if message was already handled, False otherwise
        """
        if message_ts in self.handled_messages_ts:
            return True

        # Clear set before adding if it exceeds 100 entries
        if len(self.handled_messages_ts) > 100:
            self.handled_messages_ts.clear()

        self.handled_messages_ts.add(message_ts)
        return False

    async def shutdown(self) -> None:
        """Gracefully shutdown the bot.

        Args:
            timeout: Maximum time to wait for active conversations to complete
        """
        # Stop accepting new connections first
        if self._handler:
            try:
                logger.debug("Closing socket mode handler...")
                await self._handler.close_async()
            except Exception as e:
                logger.error(f"Error closing socket handler: {e}")

        # Delegate to concurrency manager for task/thread/queue cleanup
        await self._concurrency.shutdown()

    async def start(self) -> None:
        """Start the bot using Socket Mode."""
        logger.info("Starting Slack bot in Socket Mode...")

        # Fetch bot identity and workspace emojis before starting
        await self._fetch_bot_info()
        await self._fetch_emojis()

        self._handler = AsyncSocketModeHandler(self.app, self.app_token)

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def signal_handler() -> None:
            logger.info("Received shutdown signal")
            shutdown_event.set()

        # Register signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda _s, _f: signal_handler())

        try:
            # Start the handler without blocking
            await self._handler.connect_async()
            logger.info("Bot is now running. Press Ctrl+C to stop.")

            # Wait for shutdown signal
            await shutdown_event.wait()
        finally:
            # Cleanup signal handlers
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, ValueError):
                    pass

            await self.shutdown()


async def run_bot(
    slack_bot_token: Optional[str] = None,
    slack_app_token: Optional[str] = None,
    reply_in_thread: bool = True,
    broadcast_to_channel: bool = False,
    authorized_users: Optional[List[str]] = None,
    openai_api_key: Optional[str] = None,
    config_path: Optional[str] = None,
    ignore_channels: Optional[List[str]] = None,
    only_channels: Optional[List[str]] = None,
    slack_format: SlackFormat = SlackFormat.DEFAULT,
) -> None:
    """Run the Slack bot.

    Args:
        slack_bot_token: Slack bot token (xoxb-...). If None, uses SLACK_BOT_TOKEN env var
        slack_app_token: Slack app token (xapp-...). If None, uses SLACK_APP_TOKEN env var
        reply_in_thread: If True, reply in thread. If False, reply in main channel
        broadcast_to_channel: If True and reply_in_thread is True, also show in main channel
        authorized_users: List of user IDs authorized to run commands. If None, all users are authorized
        openai_api_key: OpenAI API key for LLM-based command detection. If None, uses OPENAI_API_KEY env var
        ignore_channels: List of channel IDs to ignore messages from
        only_channels: List of channel IDs to only process messages from
        slack_format: Slack message format (DEFAULT or COMPACT)
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
        ignore_channels=ignore_channels,
        only_channels=only_channels,
        slack_format=slack_format,
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
