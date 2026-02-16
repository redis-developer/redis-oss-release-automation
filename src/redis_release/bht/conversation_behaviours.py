import asyncio
import logging
import re
import threading
import uuid
from typing import Optional

import yaml
from click import confirmation_option
from openai import OpenAI
from py_trees.common import Status
from slack_sdk import WebClient

from ..config import Config
from ..conversation_models import (
    CONFIRMATION_YAML_MARKER,
    IGNORE_THREAD_MESSAGE,
    BotQueueItem,
    BotReply,
    Command,
    CommandDetectionResult,
    ConversationCockpit,
    UserIntent,
)
from ..logging_config import set_log_prefix
from ..models import ReleaseArgs, ReleaseType
from ..state_manager import S3StateStorage, StateManager
from ..state_slack import init_slack_printer
from .behaviours import ReleaseAction
from .conversation_helpers import ArgsHelper, ConfirmationHelper, ConversationHelper
from .conversation_state import ConversationState
from .tree import async_tick_tock, initialize_tree_and_state

logger = logging.getLogger(__name__)


class ExtractArgsFromConfirmation(ReleaseAction, ConfirmationHelper):
    """Extract LLMReleaseArgs from confirmation yaml and set state.user_release_args."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        self.cockpit = cockpit
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        args = self.extract_confirmation_args()
        if args is not None:
            self.state.user_release_args = args
            self.feedback_message = f"Extracted release args: {args.release_tag}"
            return Status.SUCCESS
        self.feedback_message = "Failed to extract confirmation args"
        self.logger.info(self.feedback_message)
        self.state.replies.append(BotReply(text=self.feedback_message))
        return Status.FAILURE


class RunStatusCommand(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        self.config = config
        self.cockpit = cockpit
        super().__init__(name, log_prefix)

    def update(self) -> Status:
        self.logger.debug("RunStatusCommand - loading and posting release status")

        if not self.state.release_args:
            self.feedback_message = "No release args available"
            self.logger.warning(self.feedback_message)
            return Status.FAILURE

        self.state.command_started = True

        release_args = self.state.release_args

        self.logger.info(f"Loading status for tag {release_args.release_tag}")

        try:
            # Load state from S3 in read-only mode
            with StateManager(
                storage=S3StateStorage(),
                config=self.config,
                args=release_args,
                read_only=True,
            ) as state_manager:
                # Check if state exists
                loaded_state = state_manager.load()
                if loaded_state is None:
                    self.logger.info(
                        f"No release state found for tag {release_args.release_tag}"
                    )
                    self.state.replies.append(
                        BotReply(
                            text=f"No release state found for tag `{release_args.release_tag}`. "
                            "This release may not have been started yet."
                        )
                    )
                    return Status.SUCCESS

                # Post status to Slack if slack_args are available
                if self.state.slack_args and self.state.slack_args.bot_token:
                    self.logger.info("Posting status to Slack")
                    printer = init_slack_printer(
                        slack_token=self.state.slack_args.bot_token,
                        slack_channel_id=self.state.slack_args.channel_id,
                        thread_ts=self.state.slack_args.thread_ts,
                        reply_broadcast=self.state.slack_args.reply_broadcast,
                        slack_format=self.state.slack_args.format,
                    )
                    blocks = printer.make_blocks(state_manager.state)
                    printer.update_message(blocks)
                    printer.stop()
                    self.logger.info("Status posted to Slack")
                    self.state.replies.append(
                        BotReply(
                            text=f"Status for tag `{release_args.release_tag}` posted to Slack."
                        )
                    )
                else:
                    self.logger.info("No Slack args available, skipping Slack post")
                    self.state.replies.append(
                        BotReply(
                            text=f"Status for tag `{release_args.release_tag}` loaded successfully. "
                            "(Slack posting not configured)"
                        )
                    )

                return Status.SUCCESS

        except Exception as e:
            self.logger._logger.error(
                f"Error loading status for tag {release_args.release_tag}: {e}",
                exc_info=True,
            )
            self.state.replies.append(
                BotReply(
                    text=f"Failed to load status for tag `{release_args.release_tag}`: {str(e)}"
                )
            )
            return Status.FAILURE


class RunReleaseCommand(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        self.config = config
        self.cockpit = cockpit
        super().__init__(name, log_prefix)

    def generate_state_name(self) -> str:
        """Generate a state name using slack_ts if available, otherwise a random ID."""
        if self.state.message and self.state.message.slack_ts:
            return f"custom-slack-{self.state.message.slack_ts}"
        return f"custom-{uuid.uuid4().hex[:8]}"

    def run_release_in_thread(
        self,
        release_args: ReleaseArgs,
        stop_event: Optional[threading.Event],
    ) -> None:
        """Run release in a separate thread with its own event loop.

        Args:
            release_args: The release arguments
            stop_event: Optional event to signal graceful shutdown
        """
        log_prefix = ConversationHelper.conversation_log_prefix(self.state)
        if log_prefix:
            set_log_prefix(log_prefix)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        client: Optional[WebClient] = None
        if self.state.slack_args and self.state.slack_args.bot_token:
            client = WebClient(self.state.slack_args.bot_token)

        # Create async shutdown event and bridge from threading.Event
        shutdown_event = asyncio.Event()

        def watch_for_shutdown() -> None:
            """Watch for stop signal and set async event."""
            if stop_event:
                stop_event.wait()
                loop.call_soon_threadsafe(shutdown_event.set)

        # Start watcher thread if we have a stop event
        if stop_event:
            watcher = threading.Thread(
                target=watch_for_shutdown,
                name=f"shutdown-watcher-{release_args.release_tag}",
                daemon=True,
            )
            watcher.start()

        try:
            with initialize_tree_and_state(self.config, release_args) as (tree, _):
                loop.run_until_complete(
                    async_tick_tock(tree, cutoff=2000, shutdown_event=shutdown_event)
                )
            self.logger.info(f"Release {release_args.release_tag} completed")

        except Exception as e:
            self.logger._logger.error(
                f"Error running release {release_args.release_tag}: {e}",
                exc_info=True,
            )
            if (
                client
                and self.state.slack_args
                and self.state.slack_args.channel_id
                and self.state.slack_args.thread_ts
            ):
                client.chat_postMessage(
                    channel=self.state.slack_args.channel_id,
                    thread_ts=self.state.slack_args.thread_ts,
                    text=f"Release `{release_args.release_tag}` failed with error: {str(e)}",
                )
        finally:
            loop.close()
            # Unregister thread from concurrency manager
            if self.cockpit.concurrency_manager:
                self.cockpit.concurrency_manager.unregister_thread(
                    threading.current_thread()
                )

    def check_authorization(self, release_args: ReleaseArgs) -> bool:
        """Check if the user is authorized to run the release.

        Args:
            release_args: The release arguments

        Returns:
            True if authorized, False otherwise
        """
        if (
            self.state.authorized_users
            and release_args.custom_build is False
            and self.state.message
            and self.state.message.user not in self.state.authorized_users
        ):
            self.logger.warning(
                f"Unauthorized attempt by user {self.state.message.user}. "
                f"Authorized users: {self.state.authorized_users}"
            )
            self.state.replies.append(
                BotReply(
                    text="Sorry, you are not authorized to run releases. "
                    "Please contact an administrator."
                )
            )
            return False
        return True

    def update(self) -> Status:
        self.logger.debug("RunCommand - starting release execution")

        if not self.state.release_args:
            self.feedback_message = "No release args available"
            self.logger.warning(self.feedback_message)
            return Status.FAILURE

        self.state.command_started = True

        release_args = self.state.release_args

        if not self.check_authorization(release_args):
            return Status.FAILURE

        if release_args.custom_build:
            if self.state.state_name:
                release_args.override_state_name = self.state.state_name
            else:
                release_args.override_state_name = self.generate_state_name()
                self.logger.info(
                    f"Custom build requested, generated new state name for {release_args.release_tag}: {release_args.override_state_name}"
                )

        self.logger.info(
            f"Starting release for tag {release_args.release_tag} in background thread"
        )

        assert self.cockpit.concurrency_manager
        # Register thread and get stop event for graceful shutdown
        stop_event: Optional[threading.Event] = None
        # Create a placeholder thread to register first
        release_thread = threading.Thread(
            target=lambda: None,
            name=f"release-{release_args.override_state_name or release_args.release_tag}",
            daemon=True,
        )
        stop_event = self.cockpit.concurrency_manager.register_thread(release_thread)
        # Update thread target and start
        release_thread._target = lambda: self.run_release_in_thread(  # type: ignore[attr-defined]
            release_args, stop_event
        )
        release_thread.start()

        self.logger.info(f"Started release thread for tag {release_args.release_tag}")

        return Status.SUCCESS


class ShowConfirmationMessage(ReleaseAction):
    """Shows confirmation message for RELEASE command when not yet confirmed."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        # Check if command is RELEASE and not confirmed
        if self.state.command == Command.RELEASE and not self.state.is_confirmed:
            # Build confirmation message with current arguments
            if self.state.user_release_args:
                args = self.state.user_release_args
                # Convert to dict and filter out empty fields
                args_dict = args.model_dump(exclude_none=True)
                # Filter out empty lists, dicts, and None values
                filtered_dict = {
                    k: v
                    for k, v in args_dict.items()
                    if v is not None and v != [] and v != {} and v is not False
                }
                # Generate YAML output
                yaml_output = yaml.dump(
                    filtered_dict, default_flow_style=False, sort_keys=False
                )
                message = ""
                if self.state.state_name and not args.force_rebuild:
                    action_name = "custom build" if args.custom_build else "release"
                    message += (
                        f"> You are about to start {action_name} using existing state."
                        + " Previously completed workflows may not be re-triggered."
                        + " To re-run, ask to force rebuild specific or all packages."
                        + "\n>Alternatively start a new thread.\n\n"
                    )
                message += f"```\n{CONFIRMATION_YAML_MARKER}\n{yaml_output}```\n"

                self.logger.info("Showing confirmation message")
                self.state.replies.append(BotReply(text=message))
            else:
                self.logger.warning(
                    "Release command detected but no release arguments available"
                )
                self.state.replies.append(
                    BotReply(
                        text="Release command detected but no release arguments available. "
                        "Please provide release details."
                    )
                )

            return Status.SUCCESS

        return Status.FAILURE


class IgnoreThread(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.cockpit = cockpit
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.info("Ignoring thread")
        self.state.replies.append(BotReply(text=IGNORE_THREAD_MESSAGE))
        return Status.SUCCESS


class ExtractDetailsFromContext(ReleaseAction, ArgsHelper):
    """Extract details like state_name from context messages."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        self.config = config
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        state_name = self.extract_state_name_from_context()
        if state_name:
            self.state.state_name = state_name
            self.feedback_message = f"Extracted state name: {state_name}"
            self.logger.info(self.feedback_message)
        return Status.SUCCESS


# Conditions


class HasReleaseArgs(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.release_args:
            return Status.SUCCESS
        return Status.FAILURE


class IsCommandStarted(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.command_started:
            return Status.SUCCESS
        return Status.FAILURE


class NeedConfirmation(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if (
            self.state.llm_confirmation_required
            and self.state.command == Command.RELEASE
            and self.state.release_args
            and not self.state.is_confirmed
        ):
            self.logger.info("Confirmation required")
            return Status.SUCCESS
        return Status.FAILURE


class HasIntent(ReleaseAction):
    """Check if user intent has been detected."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.user_intent is not None:
            return Status.SUCCESS
        return Status.FAILURE


class IsIntent(ReleaseAction):
    """Check if user intent matches the specified intent."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        intent: UserIntent,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        self.intent = intent
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.user_intent == self.intent:
            return Status.SUCCESS
        return Status.FAILURE


class HasUserReleaseArgs(ReleaseAction):
    """Check if state.user_release_args is not None."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.user_release_args is not None:
            return Status.SUCCESS
        return Status.FAILURE


class HasConfirmationRequest(ReleaseAction, ConfirmationHelper):
    """Check if previous bot message contains a confirmation request."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.is_confirmation_request():
            return Status.SUCCESS
        return Status.FAILURE


class IsCommand(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        command: Command,
        log_prefix: str = "",
    ) -> None:
        self.state = state
        self.command = command
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.command == self.command:
            return Status.SUCCESS
        return Status.FAILURE
