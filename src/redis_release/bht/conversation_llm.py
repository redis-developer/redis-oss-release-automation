from pprint import pformat
from typing import cast

from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.response_input_param import ResponseInputParam
from py_trees.common import Status

from redis_release.bht.behaviours import ReleaseAction
from redis_release.bht.conversation_behaviours import logger
from redis_release.bht.conversation_helpers import LLMConvertHelper, LLMInputHelper
from redis_release.bht.conversation_state import ConversationState
from redis_release.config import Config, LLMInstructions
from redis_release.conversation_models import (
    INSTRUCTION_SNIPPETS,
    INTENT_DESCRIPTIONS,
    ActionResolutionResult,
    BotReaction,
    BotReply,
    Command,
    ConfirmationResult,
    ConversationCockpit,
    NoActionResolutionResult,
    QuestionResolutionResult,
    UserIntent,
    UserIntentDetectionResult,
)


class LLMHandleConfirmation(ReleaseAction, LLMInputHelper, LLMConvertHelper):
    """Handle user confirmation using LLM.

    Checks if user message is a confirmation (yes) or rejection (no).
    On confirmation: converts user_release_args to release_args, sets command to RELEASE, is_confirmed to True.
    On rejection: sets user_intent to ACTION to allow re-processing.
    """

    # model = "gpt-4.1-2025-04-14"
    # model = "gpt-5-nano"
    model = "gpt-4.1-nano"

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
        super().__init__(name=f"{name}\n({self.model})", log_prefix=log_prefix)

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
                model=self.model,
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

    # model = "gpt-4.1-2025-04-14"
    # model = "gpt-5-nano"
    model = "gpt-4.1-mini"

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
        super().__init__(name=f"{name}\n({self.model})", log_prefix=log_prefix)

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
                model=self.model,
                input=input,
                text_format=UserIntentDetectionResult,
            )

            self.logger.debug(f"LLM Intent response: {response}")

            result = cast(UserIntentDetectionResult, response.output_parsed)
            if not result:
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

    model = "gpt-4.1-2025-04-14"
    # model = "gpt-5-nano"
    # model = "gpt-5-mini"

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
        super().__init__(name=f"{name}\n({self.model})", log_prefix=log_prefix)

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
        Always use emoji NAMES and not unicode characters.

        List of available emojis: {self.state.emojis if self.state.emojis else "none"}

        {INSTRUCTION_SNIPPETS["slack_format"] if self.state.slack_format_is_available else ""}
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
                model=self.model,
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

    model = "gpt-4.1-2025-04-14"
    # model = "gpt-5-nano"
    # model = "gpt-4.1-mini"
    # model = "gpt-4o"

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
        super().__init__(name=f"{name}\n({self.model})", log_prefix=log_prefix)

    def instructions(self) -> str:
        commands_list = self.get_commands_list()
        modules_list = self.get_modules_list()
        # Get available packages from config with descriptions
        packages_list = LLMInstructions.packages_list_with_descriptions(self.config)
        confirmation_instructions = ""
        if self.state.llm_confirmation_required:
            confirmation_instructions = """
                For release and custom_build commands, you MUST ask the user to confirm the action.
                Your reply should:
                1. Summarize what you understood (release tag, custom build vs release, any module versions)
                2. End with a clear confirmation question like "Should I proceed?" or "Please confirm to start."

                IMPORTANT: The user has NOT confirmed yet. Do NOT say the action is starting or will be executed.
                The detected arguments will be shown in YAML format after your reply automatically.
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

        Available packages:
{packages_list}

        ***
        Provide a reply message to confirm the detected action with the user.
        {confirmation_instructions}
        ***

        You can also react with an emoji from the available list if appropriate.
        Always use emoji NAMES and not unicode characters.

        List of available emojis: {self.state.emojis if self.state.emojis else "none"}

        {INSTRUCTION_SNIPPETS["slack_format"] if self.state.slack_format_is_available else ""}
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
                model=self.model,
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

            # Set release args
            if result.release_args:
                self.set_release_args_from_llm(result.release_args)
                self.state.user_release_args = result.release_args

            # Set status args
            if result.status_args:
                self.set_release_args_from_status(result.status_args)

            # Add reply if provided
            if result.reply and self.state.command != Command.IGNORE_THREAD:
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

    # model = "gpt-4.1-2025-04-14"
    # model = "gpt-5-nano"
    model = "gpt-4.1-nano"

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
        super().__init__(name=f"{name}\n({self.model})", log_prefix=log_prefix)

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
                model=self.model,
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
