import asyncio
import logging
import threading
from pprint import pformat, pp
from typing import Dict, List, Optional, cast

from click import confirmation_option
from openai import OpenAI
from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.response_input_param import ResponseInputParam
from py_trees.common import Status
from slack_sdk import WebClient

from redis_release.conversation_models import CommandDetectionResult

from ..config import Config
from ..conversation_models import (
    COMMAND_DESCRIPTIONS,
    IGNORE_THREAD_MESSAGE,
    Command,
    CommandDetectionResult2,
    ConversationCockpit,
)
from ..models import ReleaseArgs, ReleaseType
from ..state_manager import S3StateStorage, StateManager
from ..state_slack import init_slack_printer
from .behaviours import ReleaseAction
from .conversation_state import ConversationState
from .tree import async_tick_tock, initialize_tree_and_state

logger = logging.getLogger(__name__)


class SimpleCommandClassifier(ReleaseAction):
    """Manual mode for command classification.

    The idea is to take first(after manual) word and map it to command and the rest to interpret as input yaml

    It should be triggered either if LLM is not available or if user message starts with manual.

    TODO: This is not implemented yet.
    """

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
        # Extract first word from message if available
        if self.state.message and self.state.message.message:
            first_word = self.state.message.message.strip().split()[0].lower()

            # Map first word to Command enum
            command_map = {
                "release": Command.RELEASE,
                "status": Command.STATUS,
                "help": Command.HELP,
            }

            # Set command if detected, otherwise leave as is
            if first_word in command_map:
                self.state.command = command_map[first_word]

        return Status.SUCCESS


class LLMCommandClassifier(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
        confidence_threshold: float = 0.7,
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.confidence_threshold = confidence_threshold
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.debug(f"state : {self.state.model_dump()}")
        # Check if message is available
        if not self.state.message or not self.state.message.message:
            return Status.FAILURE

        # Prepare prompt with available commands
        commands_list = []
        for cmd in Command:
            commands_list.append(f"- {cmd.value}: {COMMAND_DESCRIPTIONS[cmd]}")
        commands_list_txt = "\n".join(commands_list)

        confirmation_instructions = ""
        if self.state.llm_confirmation_required:
            confirmation_instructions = """
User confirmation is required for release command.
This means that actual command arguments may have been printed in the previous response.
If so use them to create arguments. Set is_confirmed field if user has confirmed.

Set release_args whenever user has provided enough information to execute the command even if not yet confirmed.

"""

        instructions = f"""You are a router for a Redis release and custom build automation CLI assistant.

Given the user message (and optional history), decide what of the available commands the user wants to run:
{commands_list_txt}

{confirmation_instructions}

For release command, extract the following information from the user's message:
- release_tag: The release tag (e.g., "8.4-m01-int1", "7.2.5")
- only_packages: List of specific packages to process (e.g., ["docker", "debian"]), does not have all value
- force_rebuild: List of package names to force rebuild, or ["all"] to rebuild all packages

Note force_rebuild is unlikely to be used, set it only if user explicitly asked
to rebuild specific packages. Force rebuild means start package building from
beginning, or from scratch.
If any packages are mentioned it's likely for only_packages.


For status command, extract the following information from the user's message:
- release_tag: The release tag (e.g., "8.4-m01-int1", "7.2.5")

For ignore_thread command just set the command to ignore_thread.

Available package types: docker, debian, rpm, homebrew, snap


Output using the provided JSON schema fields:
- confidence: float between 0 and 1
- command: optional command name (release, status, help)
- release_args: optional LLMReleaseArgs for release command execution
- status_args: optional StatusArgs for release command execution
- reply: optional natural language text to send back"""

        logger.debug(f"LLM instructions: {instructions}")

        # Build context history
        history_text = ""
        if self.state.message.context:
            history_text = "\n\nPrevious messages:\n" + "\n".join(
                self.state.message.context[:-1]
            )

        try:
            assert self.llm is not None
            response = self.llm.responses.parse(
                model="gpt-4o-2024-08-06",
                input=[
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": f"{self.state.message.message}{history_text}",
                    },
                ],
                text_format=CommandDetectionResult,
            )

            self.logger.debug(f"LLM response: {response}")

            result = cast(CommandDetectionResult, response.output_parsed)
            if not result:
                self.feedback_message = "LLM returned empty response"
                self.state.command = Command.HELP
                self.state.replies.append(
                    "I couldn't process your request. Please try again."
                )
                return Status.FAILURE

            confidence = result.confidence
            command = result.command
            reply = result.reply

            self.feedback_message = (
                f"LLM detected: command={command} (confidence: {confidence:.2f})"
            )

            if confidence < self.confidence_threshold:
                self.feedback_message += (
                    f" [Below threshold {self.confidence_threshold}]"
                )
                self.state.command = Command.HELP
                self.state.replies.append(
                    reply
                    or f"I'm not confident enough (confidence: {confidence:.2f}). Please clarify your request."
                )
                return Status.FAILURE

            try:
                self.state.command = command

                if command == Command.HELP:
                    self.state.replies.append(
                        reply or "How can I help you with Redis release automation?"
                    )
                    return Status.SUCCESS
                elif command == Command.STATUS:
                    if result.status_args:
                        self.state.release_args = ReleaseArgs(
                            release_tag=result.status_args.release_tag,
                        )
                        self.logger.debug(
                            f"Parsed ReleaseArgs: {self.state.release_args.model_dump_json()}"
                        )
                        return Status.SUCCESS
                    else:
                        self.state.replies.append(
                            reply or "Please provide release tag to check status."
                        )
                        return Status.FAILURE
                elif command == Command.RELEASE:
                    if result.release_args:

                        self.state.release_args = ReleaseArgs(
                            release_tag=result.release_args.release_tag,
                            force_rebuild=result.release_args.force_rebuild,
                            only_packages=result.release_args.only_packages,
                        )
                        if self.state.slack_args:
                            self.state.release_args.slack_args = self.state.slack_args

                        self.state.is_confirmed = result.is_confirmed

                        self.logger.debug(
                            f"Parsed ReleaseArgs: {self.state.release_args.model_dump_json()}, confirmed: {self.state.is_confirmed}"
                        )
                        return Status.SUCCESS
                    else:
                        self.state.replies.append(
                            reply
                            or "Please provide release tag and other required information."
                        )
                        return Status.FAILURE
                elif command == Command.IGNORE_THREAD:
                    self.state.command = Command.IGNORE_THREAD
                    self.state.replies.append(IGNORE_THREAD_MESSAGE)
                    return Status.SUCCESS
                else:
                    self.state.replies.append(
                        reply or "I'm not sure what you want me to do."
                    )
                    return Status.FAILURE
            except ValueError as e:
                self.feedback_message = f"Failed to parse command: {e}"
                self.state.command = Command.HELP
                self.state.replies.append(
                    reply or f"Failed to handle command: {command}, error: {e}"
                )
                return Status.FAILURE
        except Exception as e:
            self.feedback_message = f"LLM command detection failed: {str(e)}"
            self.state.command = Command.HELP
            self.state.replies.append(f"An error occurred: {str(e)}")
        return Status.FAILURE


class LLMCommandClassifier2(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def instructions(self) -> str:
        commands_list = []
        for cmd in Command:
            commands_list.append(f"- {cmd.value}: {COMMAND_DESCRIPTIONS[cmd]}")
        commands_list_txt = "\n".join(commands_list)

        instructions = f"""You are a bot that runs custom build process or release process for Redis.

        Given the user message, decide what of the available commands the user wants to run.

        List of available commands:
        {commands_list_txt}

        Use the actual user message to decide what command to run.
        Conversation messages are used only to help to clarify the command if it is not clear from the user message. Like if user asks to repeat the process.

        If it's likely that user comments on the results or his message is a part of conversation with another user then detect command as {Command.SKIP_MESSAGE.value}
        """
        return instructions

    def update(self) -> Status:
        try:
            assert self.llm is not None
            instructions = self.instructions()
            logger.debug(f"LLM instructions: {instructions}")
            input: ResponseInputParam = []
            input.append(EasyInputMessageParam(role="system", content=instructions))
            if self.state.message is not None:
                input.append(
                    EasyInputMessageParam(
                        role="user", content=f"{self.state.message.message}"
                    )
                )
            if self.state.context:
                for msg in self.state.context:
                    if msg.is_bot:
                        input.append(
                            EasyInputMessageParam(
                                role="assistant", content=f"{msg.message}"
                            )
                        )
                    else:
                        input.append(
                            EasyInputMessageParam(role="user", content=f"{msg.message}")
                        )
            logger.debug(f"LLM input: " + pformat(input))
            response = self.llm.responses.parse(
                model="gpt-4o-2024-08-06",
                input=input,
                text_format=CommandDetectionResult2,
            )

            self.logger.debug(f"LLM response: {response}")

            result = cast(CommandDetectionResult2, response.output_parsed)
            if not result:
                self.feedback_message = "LLM returned empty response"
                self.state.command = Command.HELP
                self.state.replies.append(
                    "I couldn't process your request. Please try again."
                )
                return Status.FAILURE

            self.state.replies.append(pformat(result))
        except Exception as e:
            self.feedback_message = f"LLM command detection failed: {str(e)}"
            self.state.command = Command.HELP
            self.state.replies.append(f"An error occurred: {str(e)}")

        return Status.FAILURE


class LLMExtractDetails(ReleaseAction):
    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        log_prefix: str = "",
        confidence_threshold: float = 0.7,
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.confidence_threshold = confidence_threshold
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        self.logger.debug(f"state : {self.state.model_dump()}")
        # Check if message is available
        if not self.state.message or not self.state.message.message:
            return Status.FAILURE

        # Prepare prompt with available commands
        commands_list = []
        for cmd in Command:
            commands_list.append(f"- {cmd.value}: {COMMAND_DESCRIPTIONS[cmd]}")
        commands_list_txt = "\n".join(commands_list)

        instructions = f"""You need to extact arguments for {self.state.command}

Given the user message
{self.state.message.message}


For release command, extract the following information from the user's message:
- release_tag: The release tag (e.g., "8.4-m01-int1", "7.2.5")
- only_packages: List of specific packages to process (e.g., ["docker", "debian"]), does not have all value
- force_rebuild: List of package names to force rebuild, or ["all"] to rebuild all packages

Note force_rebuild is unlikely to be used, set it only if user explicitly asked
to rebuild specific packages. Force rebuild means start package building from
beginning, or from scratch.
If any packages are mentioned it's likely for only_packages.


For status command, extract the following information from the user's message:
- release_tag: The release tag (e.g., "8.4-m01-int1", "7.2.5")

For ignore_thread command just set the command to ignore_thread.

Available package types: docker, debian, rpm, homebrew, snap


Output using the provided JSON schema fields:
- confidence: float between 0 and 1
- command: optional command name (release, status, help)
- release_args: optional LLMReleaseArgs for release command execution
- status_args: optional StatusArgs for release command execution
- reply: optional natural language text to send back"""

        logger.debug(f"LLM instructions: {instructions}")

        # Build context history
        history_text = ""
        if self.state.message.context:
            history_text = "\n\nPrevious messages:\n" + "\n".join(
                self.state.message.context[:-1]
            )

        try:
            assert self.llm is not None
            response = self.llm.responses.parse(
                model="gpt-4o-2024-08-06",
                input=[
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": f"{self.state.message.message}{history_text}",
                    },
                ],
                text_format=CommandDetectionResult,
            )

            self.logger.debug(f"LLM response: {response}")

            result = cast(CommandDetectionResult, response.output_parsed)
            if not result:
                self.feedback_message = "LLM returned empty response"
                self.state.command = Command.HELP
                self.state.replies.append(
                    "I couldn't process your request. Please try again."
                )
                return Status.FAILURE

            confidence = result.confidence
            command = result.command
            reply = result.reply

            self.feedback_message = (
                f"LLM detected: command={command} (confidence: {confidence:.2f})"
            )

            if confidence < self.confidence_threshold:
                self.feedback_message += (
                    f" [Below threshold {self.confidence_threshold}]"
                )
                self.state.command = Command.HELP
                self.state.replies.append(
                    reply
                    or f"I'm not confident enough (confidence: {confidence:.2f}). Please clarify your request."
                )
                return Status.FAILURE

            try:
                self.state.command = command

                if command == Command.HELP:
                    self.state.replies.append(
                        reply or "How can I help you with Redis release automation?"
                    )
                    return Status.SUCCESS
                elif command == Command.STATUS:
                    if result.status_args:
                        self.state.release_args = ReleaseArgs(
                            release_tag=result.status_args.release_tag,
                        )
                        self.logger.debug(
                            f"Parsed ReleaseArgs: {self.state.release_args.model_dump_json()}"
                        )
                        return Status.SUCCESS
                    else:
                        self.state.replies.append(
                            reply or "Please provide release tag to check status."
                        )
                        return Status.FAILURE
                elif command == Command.RELEASE:
                    if result.release_args:

                        self.state.release_args = ReleaseArgs(
                            release_tag=result.release_args.release_tag,
                            force_rebuild=result.release_args.force_rebuild,
                            only_packages=result.release_args.only_packages,
                        )
                        if self.state.slack_args:
                            self.state.release_args.slack_args = self.state.slack_args

                        self.state.is_confirmed = result.is_confirmed

                        self.logger.debug(
                            f"Parsed ReleaseArgs: {self.state.release_args.model_dump_json()}, confirmed: {self.state.is_confirmed}"
                        )
                        return Status.SUCCESS
                    else:
                        self.state.replies.append(
                            reply
                            or "Please provide release tag and other required information."
                        )
                        return Status.FAILURE
                elif command == Command.IGNORE_THREAD:
                    self.state.command = Command.IGNORE_THREAD
                    self.state.replies.append(IGNORE_THREAD_MESSAGE)
                    return Status.SUCCESS
                else:
                    self.state.replies.append(
                        reply or "I'm not sure what you want me to do."
                    )
                    return Status.FAILURE
            except ValueError as e:
                self.feedback_message = f"Failed to parse command: {e}"
                self.state.command = Command.HELP
                self.state.replies.append(
                    reply or f"Failed to handle command: {command}, error: {e}"
                )
                return Status.FAILURE
        except Exception as e:
            self.feedback_message = f"LLM command detection failed: {str(e)}"
            self.state.command = Command.HELP
            self.state.replies.append(f"An error occurred: {str(e)}")
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
            return Status.FAILURE

        if self.state.command != Command.STATUS:
            self.feedback_message = "Command is not STATUS"
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
                        f"No release state found for tag `{release_args.release_tag}`. "
                        "This release may not have been started yet."
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
                    printer.update_message(state_manager.state)
                    self.state.replies.append(
                        f"Status for tag `{release_args.release_tag}` posted to Slack."
                    )
                else:
                    self.logger.info("No Slack args available, skipping Slack post")
                    self.state.replies.append(
                        f"Status for tag `{release_args.release_tag}` loaded successfully. "
                        "(Slack posting not configured)"
                    )

                return Status.SUCCESS

        except Exception as e:
            self.logger._logger.error(
                f"Error loading status for tag {release_args.release_tag}: {e}",
                exc_info=True,
            )
            self.state.replies.append(
                f"Failed to load status for tag `{release_args.release_tag}`: {str(e)}"
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
        super().__init__(name, log_prefix)

    def update(self) -> Status:
        self.logger.debug("RunCommand - starting release execution")

        if not self.state.release_args:
            self.feedback_message = "No release args available"
            return Status.FAILURE

        if self.state.command != Command.RELEASE:
            self.feedback_message = "Command is not RELEASE"
            return Status.FAILURE

        self.state.command_started = True

        release_args = self.state.release_args

        # Check authorization
        if (
            self.state.authorized_users
            and self.state.message
            and self.state.message.user not in self.state.authorized_users
        ):
            logger.warning(
                f"Unauthorized attempt by user {self.state.message.user}. Authorized users: {self.state.authorized_users}"
            )
            self.state.replies.append(
                "Sorry, you are not authorized to run releases. Please contact an administrator."
            )
            return Status.FAILURE

        self.logger.info(
            f"Starting release for tag {release_args.release_tag} in background thread"
        )

        # Start release in a separate thread
        def run_release_in_thread() -> None:
            """Run release in a separate thread with its own event loop."""
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client: Optional[WebClient] = None
            if self.state.slack_args and self.state.slack_args.bot_token:
                client = WebClient(self.state.slack_args.bot_token)

            try:
                # Run the release
                with initialize_tree_and_state(self.config, release_args) as (
                    tree,
                    _,
                ):
                    loop.run_until_complete(async_tick_tock(tree, cutoff=2000))

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

        # Start the thread
        release_thread = threading.Thread(
            target=run_release_in_thread,
            name=f"release-{release_args.release_tag}",
            daemon=True,
        )
        release_thread.start()
        self.logger.info(f"Started release thread for tag {release_args.release_tag}")

        self.state.replies.append(
            f"Starting release for tag `{release_args.release_tag}`... "
            "I'll post updates as the release progresses."
        )

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
            if self.state.release_args:
                args = self.state.release_args
                message_parts = [
                    f"Please confirm the following release configuration:",
                    f"• Release tag: `{args.release_tag}`",
                ]

                if args.only_packages:
                    message_parts.append(
                        f"• Only packages: {', '.join(args.only_packages)}"
                    )

                if args.force_rebuild:
                    if args.force_rebuild == ["all"]:
                        message_parts.append(f"• Force rebuild: all packages")
                    else:
                        message_parts.append(
                            f"• Force rebuild: {', '.join(args.force_rebuild)}"
                        )

                message_parts.append("\nReply 'yes' or 'confirm' to proceed.")

                self.state.replies.append("\n".join(message_parts))
            else:
                self.state.replies.append(
                    "Release command detected but no release arguments available. "
                    "Please provide release details."
                )

            return Status.SUCCESS

        return Status.FAILURE


# Conditions


class IsLLMAvailable(ReleaseAction):
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
        if self.state.llm_available:
            return Status.SUCCESS
        return Status.FAILURE


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
            return Status.SUCCESS
        return Status.FAILURE
