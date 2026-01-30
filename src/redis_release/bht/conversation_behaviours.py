import asyncio
import logging
import threading
from pprint import pformat, pp
from typing import Dict, List, Optional, cast

import yaml
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
    INSTRUCTION_SNIPPETS,
    INTENT_DESCRIPTIONS,
    REDIS_MODULE_DESCRIPTIONS,
    ActionResolutionResult,
    BotReaction,
    BotReply,
    Command,
    CommandDetectionResult2,
    ConfirmationResult,
    ConversationCockpit,
    LLMReleaseArgs,
    LLMStatusArgs,
    NoActionResolutionResult,
    QuestionResolutionResult,
    UserIntent,
    UserIntentDetectionResult,
)
from ..models import RedisModule, ReleaseArgs, ReleaseType
from ..state_manager import S3StateStorage, StateManager
from ..state_slack import init_slack_printer
from .behaviours import ReleaseAction
from .conversation_state import ConversationState
from .tree import CUSTOM_BUILD_PACKAGES, async_tick_tock, initialize_tree_and_state

logger = logging.getLogger(__name__)


class LLMInputHelper:
    """Mixin class for building LLM input messages from conversation state.

    Classes using this mixin must have a `state: ConversationState` attribute.
    """

    state: ConversationState  # Expected to be provided by the class using this mixin

    def add_inbox_and_context(
        self,
        input: ResponseInputParam,
        include_context: bool = True,
        context_first: bool = True,
    ) -> None:
        """Add inbox message and context to LLM input.

        Args:
            input: The LLM input list to append messages to
            include_context: Whether to include context messages
            context_first: If True, add context before current message; if False, add current message first
        """
        if context_first:
            self.add_context_messages(input, include_context)
            self.add_current_message(input)
        else:
            self.add_current_message(input)
            self.add_context_messages(input, include_context)

    def add_context_messages(
        self, input: ResponseInputParam, include_context: bool = True
    ) -> None:
        """Add context messages to input if include_context is True."""
        if include_context and self.state.context:
            for msg in self.state.context:
                if msg.is_from_bot:
                    input.append(
                        EasyInputMessageParam(
                            role="assistant", content=f"{msg.message}"
                        )
                    )
                else:
                    input.append(
                        EasyInputMessageParam(role="user", content=f"{msg.message}")
                    )

    def add_current_message(self, input: ResponseInputParam) -> None:
        """Add the current user message to input."""
        if self.state.message is not None:
            input.append(
                EasyInputMessageParam(
                    role="user", content=f"{self.state.message.message}"
                )
            )

    def is_direct_mention(self) -> bool:
        """Check if the inbox message is a direct mention of the bot."""
        return self.state.message is not None and self.state.message.is_mention

    def get_commands_list(self) -> str:
        """Generate a formatted list of available commands with descriptions.

        Args:
            prefix: Optional prefix to add before each line (e.g., "        " for indentation)
        """
        return "\n".join(
            f"{cmd.value}: {desc}" for cmd, desc in COMMAND_DESCRIPTIONS.items()
        )

    def get_modules_list(self) -> str:
        """Generate a formatted list of available Redis modules with descriptions.

        Args:
            prefix: Optional prefix to add before each line (e.g., "        " for indentation)
        """
        return "\n".join(
            f"{module.value}: {desc}"
            for module, desc in REDIS_MODULE_DESCRIPTIONS.items()
        )


class LLMConvertHelper:
    """Helper class for converting LLM response args to state release args.

    Classes using this helper must have:
    - `state: ConversationState` attribute
    - `config: Config` attribute
    """

    state: ConversationState  # Expected to be provided by the class using this helper
    config: Config  # Expected to be provided by the class using this helper

    def set_release_args_from_llm(self, llm_args: LLMReleaseArgs) -> None:
        """Create state.release_args from LLMReleaseArgs.

        Raises:
            ValueError: If invalid package names are provided in force_rebuild or only_packages.
        """
        # Determine valid packages based on custom_build mode
        if llm_args.custom_build:
            valid_packages = set(CUSTOM_BUILD_PACKAGES)
        else:
            valid_packages = set(self.config.packages.keys())

        # Validate force_rebuild package names
        if llm_args.force_rebuild and llm_args.force_rebuild != ["all"]:
            invalid_force_rebuild = set(llm_args.force_rebuild) - valid_packages
            if invalid_force_rebuild:
                raise ValueError(
                    f"Invalid package names in force_rebuild: {invalid_force_rebuild}. "
                    f"Valid packages: {valid_packages}"
                )

        # Validate only_packages package names
        if llm_args.only_packages:
            invalid_only_packages = set(llm_args.only_packages) - valid_packages
            if invalid_only_packages:
                raise ValueError(
                    f"Invalid package names in only_packages: {invalid_only_packages}. "
                    f"Valid packages: {valid_packages}"
                )

        # Convert module_versions from List[ModuleVersion] to Dict[RedisModule, str]
        module_versions: Dict[RedisModule, str] = {}
        for mv in llm_args.module_versions:
            module = RedisModule(mv.module_name)
            module_versions[module] = mv.version

        self.state.release_args = ReleaseArgs(
            release_tag=llm_args.release_tag,
            force_rebuild=llm_args.force_rebuild,
            only_packages=llm_args.only_packages,
            custom_build=llm_args.custom_build,
            module_versions=module_versions,
        )
        if self.state.slack_args:
            self.state.release_args.slack_args = self.state.slack_args

    def set_release_args_from_status(self, status_args: LLMStatusArgs) -> None:
        """Create state.release_args from LLMStatusArgs."""
        self.state.release_args = ReleaseArgs(
            release_tag=status_args.release_tag,
        )
        if self.state.slack_args:
            self.state.release_args.slack_args = self.state.slack_args


class ConfirmationHelper:
    """Helper class for handling confirmation requests.

    Classes using this helper must have:
    - `state: ConversationState` attribute
    """

    state: ConversationState  # Expected to be provided by the class using this helper

    CONFIRMATION_HEADER = "# release confirmation"

    def get_previous_bot_message(self) -> Optional[str]:
        """Get the previous message from the bot in context."""
        if not self.state.context:
            return None
        # Find the last message from the bot
        for msg in reversed(self.state.context):
            if msg.is_from_bot:
                return msg.message
        return None

    def is_confirmation_request(self) -> bool:
        """Check if the previous bot message contains a confirmation request."""
        prev_message = self.get_previous_bot_message()
        if not prev_message:
            return False
        # Check if message contains the confirmation yaml header
        return "```" in prev_message and self.CONFIRMATION_HEADER in prev_message

    def extract_confirmation_args(self) -> Optional[LLMReleaseArgs]:
        """Extract LLMReleaseArgs from the previous bot message's confirmation yaml.

        Returns:
            LLMReleaseArgs if successfully parsed, None otherwise.
        """
        prev_message = self.get_previous_bot_message()
        if not prev_message:
            return None

        # Extract yaml content between ``` markers
        try:
            # Find the code block
            start_marker = "```"
            end_marker = "```"

            start_idx = prev_message.find(start_marker)
            if start_idx == -1:
                return None

            # Skip past the opening marker and any language identifier
            content_start = prev_message.find("\n", start_idx)
            if content_start == -1:
                return None
            content_start += 1

            # Find the closing marker
            end_idx = prev_message.find(end_marker, content_start)
            if end_idx == -1:
                return None

            yaml_content = prev_message[content_start:end_idx]

            # Remove the header comment if present
            lines = yaml_content.strip().split("\n")
            if lines and lines[0].strip().startswith("#"):
                lines = lines[1:]
            yaml_content = "\n".join(lines)

            # Parse the yaml
            parsed = yaml.safe_load(yaml_content)
            if not parsed or not isinstance(parsed, dict):
                return None

            # Convert to LLMReleaseArgs
            return LLMReleaseArgs(**parsed)

        except Exception as e:
            logger.warning(f"Failed to extract confirmation args: {e}")
            return None


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
        self.state.replies.append(BotReply(text=self.feedback_message))
        return Status.FAILURE


class LLMHandleConfirmation(ReleaseAction, LLMInputHelper, LLMConvertHelper):
    """Handle user confirmation using LLM.

    Checks if user message is a confirmation (yes) or rejection (no).
    On confirmation: converts user_release_args to release_args, sets command to RELEASE, is_confirmed to True.
    On rejection: sets user_intent to ACTION to allow re-processing.
    """

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.config = config
        super().__init__(name=name, log_prefix=log_prefix)

    def instructions(self) -> str:
        return """You are checking if the user is confirming or rejecting an action.

The previous message showed release details and asked for confirmation.
The user has now responded.

Determine if the user's response is:
- A confirmation, when user expresses approval, acceptance, aggrement, intention to continue with the action (yes, confirm, proceed, ok, sure, go ahead, etc.) -> is_confirmed = true
- A rejection or anything unclear (no, cancel, stop, wait, or any other response) -> is_confirmed = false

"""

    def update(self) -> Status:
        try:
            assert self.llm is not None
            instructions = self.instructions()

            input: ResponseInputParam = []
            input.append(EasyInputMessageParam(role="system", content=instructions))
            self.add_current_message(input)

            logger.debug(f"LLM Confirmation input: {pformat(input)}")
            response = self.llm.responses.parse(
                model="gpt-4.1-2025-04-14",
                input=input,
                text_format=ConfirmationResult,
            )

            self.logger.debug(f"LLM Confirmation response: {response}")

            result = cast(ConfirmationResult, response.output_parsed)
            if not result:
                self.feedback_message = "LLM returned empty response"
                return Status.FAILURE

            if result.is_confirmed:
                # User confirmed - convert args and set command
                if self.state.user_release_args:
                    self.set_release_args_from_llm(self.state.user_release_args)
                    self.state.command = Command.RELEASE
                    self.state.is_confirmed = True
                    self.feedback_message = "User confirmed release"
                    self.state.replies.append(
                        BotReply(text="Confirmed! Starting release...")
                    )
                    return Status.SUCCESS
                else:
                    self.feedback_message = "No user_release_args to confirm"
                    self.state.replies.append(
                        BotReply(
                            text="Error: could not find release arguments to confirm."
                        )
                    )
                    return Status.FAILURE
            else:
                # User rejected - set intent to ACTION to allow re-processing
                self.state.user_intent = UserIntent.ACTION
                self.feedback_message = "User rejected confirmation"
                return Status.SUCCESS

        except Exception as e:
            self.feedback_message = f"LLM confirmation handling failed: {str(e)}"
            self.state.replies.append(BotReply(text=f"An error occurred: {str(e)}"))
            return self.log_exception_and_return_failure(e)


class LLMIntentDetector(ReleaseAction, LLMInputHelper):
    """Detect user intent using LLM. Only detects intent, nothing else."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.config = config
        super().__init__(name=name, log_prefix=log_prefix)

    def instructions(self) -> str:
        # Exclude NO_ACTION if this is a direct mention - user explicitly addressed the bot
        is_mention = self.is_direct_mention()
        intent_list = "\n".join(
            f"        - {intent.value}: {desc}"
            for intent, desc in INTENT_DESCRIPTIONS.items()
            if not is_mention or intent != UserIntent.NO_ACTION
        )
        goal = "First of all understand whether the message is intended for you as a bot, or maybe it is a message for another user in the thread."
        if is_mention:
            goal += " Since user directly mentioned the bot, it is very likely that the message is intended for you."
        instructions = f"""You are analyzing a user message to detect their intent.

        {goal}

        Use only user message to detect the intent, the context is provided only to understand
        - whether there is ongoing unrelated conversation
        - to help clarify the intent if it is not clear from the message alone.

        Classify the user's intent into one of the following categories:
{intent_list}

        Only detect the intent, do not provide any other information.

        More context that could help identify the intent:
        {INSTRUCTION_SNIPPETS["bot_purpose"]}

        Commands available:
        {self.get_commands_list()}
        """
        return instructions

    def update(self) -> Status:
        try:
            assert self.llm is not None
            instructions = self.instructions()
            logger.debug(f"LLM Intent instructions: {instructions}")
            input: ResponseInputParam = []
            input.append(EasyInputMessageParam(role="system", content=instructions))
            self.add_inbox_and_context(input, include_context=True, context_first=True)
            logger.debug(f"LLM Intent input: " + pformat(input))
            response = self.llm.responses.parse(
                model="gpt-4.1-2025-04-14",
                input=input,
                text_format=UserIntentDetectionResult,
            )

            self.logger.debug(f"LLM Intent response: {response}")

            result = cast(UserIntentDetectionResult, response.output_parsed)
            if not result or result.intent is None:
                self.feedback_message = "LLM returned empty intent"
                return Status.FAILURE

            self.state.user_intent = result.intent
            self.feedback_message = f"Detected intent: {result.intent.value}"
            return Status.SUCCESS

        except Exception as e:
            self.feedback_message = f"LLM intent detection failed: {str(e)}"
            return Status.FAILURE


class LLMQuestionHandler(ReleaseAction, LLMInputHelper):
    """Handle question intent using LLM."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.config = config
        super().__init__(name=name, log_prefix=log_prefix)

    def instructions(self) -> str:
        # Include config structure for context about available packages and settings
        commands_list = self.get_commands_list()
        modules_list = self.get_modules_list()
        config_json = self.config.model_dump_json(indent=2)
        instructions = f"""You are a bot that supports release process for Redis by running builds or releases.

        The user has asked a question. Provide a helpful and concise answer.
        STRICTLY FOCUS ON ANSWERING THE QUESTION ITSELF, CONTEXT IS PROVIDED ONLY TO UNDERSTAND THE CONVERSATION.

        The following information may help to understand the question and provide a better answer:

        {INSTRUCTION_SNIPPETS["bot_purpose"]}

        The following command descriptions are used when it's clear from the question that user wants to perform a specific action: {commands_list}

        Available Redis modules: {modules_list}

        Release tags information: {INSTRUCTION_SNIPPETS["release_tags"]}

        Module versions information: {INSTRUCTION_SNIPPETS["module_versions"]}


        Here is the current configuration that defines available packages and their settings:
        ```json
{config_json}
        ```

        You can also react with an emoji from the available list if appropriate.

        List of available emojis: {self.state.emojis if self.state.emojis else "none"}
        """
        return instructions

    def update(self) -> Status:
        try:
            assert self.llm is not None
            instructions = self.instructions()
            logger.debug(f"LLM Question instructions: {instructions}")
            input: ResponseInputParam = []
            input.append(EasyInputMessageParam(role="system", content=instructions))
            self.add_inbox_and_context(input, include_context=True, context_first=False)
            logger.debug(f"LLM Question input: " + pformat(input))
            response = self.llm.responses.parse(
                model="gpt-4.1-2025-04-14",
                input=input,
                text_format=QuestionResolutionResult,
            )

            self.logger.debug(f"LLM Question response: {response}")

            result = cast(QuestionResolutionResult, response.output_parsed)
            if not result:
                self.feedback_message = "LLM returned empty response"
                self.state.replies.append(
                    BotReply(text="I couldn't process your question. Please try again.")
                )
                return Status.FAILURE

            if result.reply:
                self.state.replies.append(BotReply(text=result.reply))
            if result.emoji:
                self.state.replies.append(BotReaction(emoji=result.emoji))

            return Status.SUCCESS

        except Exception as e:
            self.feedback_message = f"LLM question handling failed: {str(e)}"
            self.state.replies.append(BotReply(text=f"An error occurred: {str(e)}"))
            return Status.FAILURE


class LLMActionHandler(ReleaseAction, LLMInputHelper, LLMConvertHelper):
    """Handle action intent using LLM. Detects command and extracts arguments."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.config = config
        super().__init__(name=name, log_prefix=log_prefix)

    def instructions(self) -> str:
        commands_list = self.get_commands_list()
        modules_list = self.get_modules_list()
        # Get available packages from config
        packages_list = ", ".join(self.config.packages.keys())
        confirmation_instructions = ""
        if self.state.llm_confirmation_required:
            confirmation_instructions = """
                WARNING:  user confirmation *is required for release command*.
                Please formulate your response in a way that considers that after the reply release details are shown to the user.
                Detected arguments would be output in YAML format after your reply automatically.
        """
        instructions = f"""You are a Redis release and custom build automation assistant.

        {INSTRUCTION_SNIPPETS["bot_purpose"]}

        The user wants to perform an action. Detect the command and extract the arguments.

        Available commands:
{commands_list}

        For release/build command, extract the following information:
        - release_tag:
            {INSTRUCTION_SNIPPETS["release_tags"]}
        - custom_build: Whether this is a custom build (True) or a release (False). Assume custom build unless release is explicitly mentioned.
        - module_versions: List of module versions if specified (e.g., [{{"module_name": "redisjson", "version": "2.4.0"}}])
            {INSTRUCTION_SNIPPETS["module_versions"]}
        - only_packages: List of specific packages to process (e.g., ["docker", "debian"])
        - force_rebuild: List of package names to force rebuild, or ["all"] to rebuild all

        Available Redis modules:
{modules_list}

        For status command, extract:
        - release_tag: The release tag to check status for

        For ignore_thread command, just set the command to ignore_thread.

        Available packages: {packages_list}

        ***
        Provide a reply message to confirm the detected action with the user.
        {confirmation_instructions}
        ***

        You can also react with an emoji from the available list if appropriate.

        List of available emojis: {self.state.emojis if self.state.emojis else "none"}
        """
        return instructions

    def update(self) -> Status:
        try:
            assert self.llm is not None
            instructions = self.instructions()
            logger.debug(f"LLM Action instructions: {instructions}")

            input: ResponseInputParam = []
            input.append(EasyInputMessageParam(role="system", content=instructions))
            self.add_inbox_and_context(input, include_context=True, context_first=True)

            logger.debug(f"LLM Action input: " + pformat(input))
            response = self.llm.responses.parse(
                model="gpt-4.1-2025-04-14",
                input=input,
                text_format=ActionResolutionResult,
            )

            self.logger.debug(f"LLM Action response: {response}")

            result = cast(ActionResolutionResult, response.output_parsed)
            if not result:
                self.feedback_message = "LLM returned empty response"
                self.state.replies.append(
                    BotReply(text="I couldn't understand your action request.")
                )
                return Status.FAILURE

            # Set command
            if result.command:
                self.state.command = result.command
                self.feedback_message = f"Detected command: {result.command.value}"
                self.state.replies.append(
                    BotReply(text=f"Command: {result.command.value}")
                )

            # Set release args
            if result.release_args:
                self.set_release_args_from_llm(result.release_args)
                self.state.user_release_args = result.release_args
                self.state.replies.append(
                    BotReply(
                        text=f"Release args: {result.release_args.model_dump_json()}"
                    )
                )

            # Set status args
            if result.status_args:
                self.set_release_args_from_status(result.status_args)

            # Add reply if provided
            if result.reply:
                self.state.replies.append(BotReply(text=result.reply))

            # Add reaction if provided
            if result.emoji:
                self.state.replies.append(BotReaction(emoji=result.emoji))

            return Status.SUCCESS

        except Exception as e:
            self.feedback_message = f"LLM action handling failed: {str(e)}"
            self.state.replies.append(BotReply(text=f"An error occurred: {str(e)}"))
            return self.log_exception_and_return_failure(e)


class LLMNoActionHandler(ReleaseAction, LLMInputHelper):
    """Handle no-action intent using LLM. Detects if we need a reaction."""

    def __init__(
        self,
        name: str,
        state: ConversationState,
        cockpit: ConversationCockpit,
        config: Config,
        log_prefix: str = "",
    ) -> None:
        self.llm = cockpit.llm
        self.state = state
        self.config = config
        super().__init__(name=name, log_prefix=log_prefix)

    def instructions(self) -> str:
        is_mention = self.is_direct_mention()
        must_react = ""
        if is_mention:
            must_react = "Since user directly mentioned the bot, you MUST at least react with an emoji to acknowledge the message, not necessarily to reply."

        instructions = f"""You are analyzing a message that doesn't require a direct response.

        Decide if you should react with an emoji to acknowledge the message.

        Only react if it makes sense (e.g., the user said something positive, acknowledged something, etc.)
        {must_react}


        List of available emojis: {self.state.emojis if self.state.emojis else "none"}
        """
        return instructions

    def update(self) -> Status:
        try:
            assert self.llm is not None
            instructions = self.instructions()
            logger.debug(f"LLM NoAction instructions: {instructions}")
            input: ResponseInputParam = []
            input.append(EasyInputMessageParam(role="system", content=instructions))
            self.add_inbox_and_context(input, include_context=True, context_first=True)
            logger.debug(f"LLM NoAction input: " + pformat(input))
            response = self.llm.responses.parse(
                model="gpt-4.1-2025-04-14",
                input=input,
                text_format=NoActionResolutionResult,
            )

            self.logger.debug(f"LLM NoAction response: {response}")

            result = cast(NoActionResolutionResult, response.output_parsed)
            if result and result.emoji:
                self.state.replies.append(BotReaction(emoji=result.emoji))

            return Status.SUCCESS

        except Exception as e:
            self.feedback_message = f"LLM no-action handling failed: {str(e)}"
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
                    printer.update_message(state_manager.state)
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
                BotReply(
                    text="Sorry, you are not authorized to run releases. Please contact an administrator."
                )
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
            BotReply(
                text=f"Starting release for tag `{release_args.release_tag}`... "
                "I'll post updates as the release progresses."
            )
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
                message = f"```\n# release confirmation\n{yaml_output}```\n"

                self.state.replies.append(BotReply(text=message))
            else:
                self.state.replies.append(
                    BotReply(
                        text="Release command detected but no release arguments available. "
                        "Please provide release details."
                    )
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


class IsQuestion(ReleaseAction):
    """Check if user intent is a question."""

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
        if self.state.user_intent == UserIntent.QUESTION:
            return Status.SUCCESS
        return Status.FAILURE


class IsAction(ReleaseAction):
    """Check if user intent is an action."""

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
        if self.state.user_intent == UserIntent.ACTION:
            return Status.SUCCESS
        return Status.FAILURE


class IsNoAction(ReleaseAction):
    """Check if user intent is no action."""

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
        if self.state.user_intent == UserIntent.NO_ACTION:
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
