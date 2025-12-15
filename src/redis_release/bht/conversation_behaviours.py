import asyncio
import logging
import threading
from typing import Dict, List, Literal, Optional

from openai import OpenAI
from py_trees.common import Status
from pydantic import BaseModel, Field
from slack_sdk import WebClient

from ..config import Config
from ..conversation_models import Command, ConversationCockpit
from ..models import ReleaseArgs, ReleaseType
from .behaviours import ReleaseAction
from .conversation_state import ConversationState
from .tree import async_tick_tock, initialize_tree_and_state

logger = logging.getLogger(__name__)


class LLMReleaseArgs(BaseModel):
    """Simplified release arguments for LLM structured output."""

    release_tag: str = Field(description="The release tag (e.g., '8.4-m01', '7.2.5')")
    force_rebuild: List[str] = Field(
        default_factory=list,
        description="List of package names to force rebuild, or ['all'] for all packages",
    )
    only_packages: List[str] = Field(
        default_factory=list,
        description="List of specific packages to process (e.g., ['docker', 'debian'])",
    )


class CommandDetectionResult(BaseModel):
    """Structured output for command detection."""

    intent: Literal["command", "info", "clarification"] = Field(
        description="Whether user wants to run a command, get info, or needs clarification"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score between 0 and 1"
    )
    command: Optional[str] = Field(
        None, description="Detected command name (release, status, custom_build, etc.)"
    )
    release_args: Optional[LLMReleaseArgs] = Field(
        None, description="Release arguments for command execution"
    )
    reply: Optional[str] = Field(
        None, description="Natural language reply to send back to user"
    )


class SimpleCommandClassifier(ReleaseAction):
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
                "custom_build": Command.CUSTOM_BUILD,
                "unstable_build": Command.UNSTABLE_BUILD,
                "status": Command.STATUS,
                "help": Command.HELP,
            }

            # Set command if detected, otherwise leave as is
            if first_word in command_map:
                self.state.command = command_map[first_word]

        # Always return SUCCESS
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
        commands_list = "\n".join([f"- {cmd.value}" for cmd in Command])

        instructions = f"""You are a router for a Redis release automation CLI assistant.

Available commands:
{commands_list}

Given the user message (and optional history), decide:
- Does the user want to run a command?
- If yes, which command and with what args?
- Otherwise, are they just asking for help/info or do they need clarification?

For command intent with non-help commands (release, status, custom_build, unstable_build), extract:
- release_tag: The release tag (e.g., "8.4-m01-int1", "7.2.5")
- force_rebuild: List of package names to force rebuild, or ["all"] to rebuild all packages
- only_packages: List of specific packages to process (e.g., ["docker", "debian"])

Available package types: docker, debian, rpm, homebrew, snap

Output using the provided JSON schema fields:
- intent: "command" | "info" | "clarification"
- confidence: float between 0 and 1
- command: optional command name (release, status, custom_build, unstable_build, help)
- release_args: optional ReleaseArgs for command execution
- reply: optional natural language text to send back"""

        # Build context history
        history_text = ""
        if self.state.message.context:
            history_text = "\n\nPrevious messages:\n" + "\n".join(
                self.state.message.context[:-1]
            )

        try:
            assert self.llm is not None
            # Call LLM with structured outputs
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

            # Extract parsed result from structured output
            result = response.output_parsed
            if not result:
                self.feedback_message = "LLM returned empty response"
                self.state.command = Command.HELP
                self.state.reply = "I couldn't process your request. Please try again."
                return Status.FAILURE

            # Extract fields from structured result
            intent = result.intent
            confidence = result.confidence
            command_value = result.command
            reply = result.reply

            # Log the detection
            self.feedback_message = f"LLM detected: intent={intent}, command={command_value} (confidence: {confidence:.2f})"

            # Check confidence threshold
            if confidence < self.confidence_threshold:
                self.feedback_message += (
                    f" [Below threshold {self.confidence_threshold}]"
                )
                self.state.command = Command.HELP
                self.state.reply = (
                    reply
                    or f"I'm not confident enough (confidence: {confidence:.2f}). Please clarify your request."
                )
                return Status.FAILURE

            # Handle based on intent
            if intent == "info" or intent == "clarification":
                self.state.command = Command.HELP
                self.state.reply = (
                    reply or "How can I help you with Redis release automation?"
                )
                return Status.SUCCESS

            # For command intent, validate and set command
            if intent == "command" and command_value:
                try:
                    command = Command(command_value)
                    self.state.command = command

                    # If help command, set reply
                    if command == Command.HELP:
                        self.state.reply = (
                            reply or "How can I help you with Redis release automation?"
                        )
                        return Status.SUCCESS

                    # For non-help commands, use release_args from structured output
                    if result.release_args:
                        # Convert LLMReleaseArgs to ReleaseArgs
                        llm_args = result.release_args

                        # Create ReleaseArgs with converted types
                        self.state.release_args = ReleaseArgs(
                            release_tag=llm_args.release_tag,
                            force_rebuild=llm_args.force_rebuild,
                            only_packages=llm_args.only_packages,
                        )
                        if self.state.slack_args:
                            self.state.release_args.slack_args = self.state.slack_args

                        self.logger.info(
                            f"Parsed ReleaseArgs: {self.state.release_args.model_dump_json()}"
                        )
                        return Status.SUCCESS
                    else:
                        # Non-help command without release_args
                        self.feedback_message = (
                            "Missing release_args for non-help command"
                        )
                        self.state.command = Command.HELP
                        self.state.reply = (
                            reply
                            or "Please provide release details (e.g., version tag)"
                        )
                        return Status.FAILURE

                except ValueError:
                    self.feedback_message = f"Invalid command value: {command_value}"
                    self.state.command = Command.HELP
                    self.state.reply = reply or f"Unknown command: {command_value}"
                    return Status.FAILURE
            else:
                self.state.command = Command.HELP
                self.state.reply = reply or "I couldn't understand your request."
                return Status.FAILURE

        except Exception as e:
            self.feedback_message = f"LLM command detection failed: {str(e)}"
            self.state.command = Command.HELP
            self.state.reply = f"An error occurred: {str(e)}"
            return Status.FAILURE


class RunCommand(ReleaseAction):
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

        # Check if we have release args
        if not self.state.release_args:
            self.feedback_message = "No release args available"
            return Status.FAILURE

        # Mark command as started
        self.state.command_started = True

        # Get release args
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
            self.state.reply = "Sorry, you are not authorized to run releases. Please contact an administrator."
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

        # Set reply to inform user
        self.state.reply = (
            f"Starting release for tag `{release_args.release_tag}`... "
            "I'll post updates as the release progresses."
        )

        return Status.SUCCESS


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
