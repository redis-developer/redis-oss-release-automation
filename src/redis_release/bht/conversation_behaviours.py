import json

from openai import OpenAI
from py_trees.common import Status

from ..conversation_models import Command
from .behaviours import ReleaseAction
from .conversation_state import ConversationState


class SimpleCommandClassifier(ReleaseAction):
    def __init__(
        self, name: str, state: ConversationState, log_prefix: str = ""
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
        llm: OpenAI,
        state: ConversationState,
        log_prefix: str = "",
        confidence_threshold: float = 0.7,
    ) -> None:
        self.llm = llm
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

        system_prompt = f"""You are a command detector for a Redis release automation system.
Your task is to analyze user messages and detect which command they want to execute.

Available commands:
{commands_list}

Respond with a JSON object containing:
- "command": the detected command value (one of the available commands, or null if uncertain)
- "confidence": a number between 0 and 1 indicating your confidence in the detection
- "reasoning": brief explanation of your decision

Example response:
{{"command": "release", "confidence": 0.95, "reasoning": "User explicitly mentioned releasing version 8.2.0"}}

If the message doesn't clearly match any command, set command to null and explain why."""

        user_message = self.state.message.message

        try:
            # Call LLM
            response = self.llm.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            self.logger.debug(f"LLM response: {response}")

            # Parse response
            content = response.choices[0].message.content
            if not content:
                self.feedback_message = "LLM returned empty response"
                return Status.FAILURE

            result = json.loads(content)
            command_value = result.get("command")
            confidence = result.get("confidence", 0.0)
            reasoning = result.get("reasoning", "")

            # Log the detection
            self.feedback_message = (
                f"LLM detected: {command_value} (confidence: {confidence:.2f})"
            )

            # Check confidence threshold
            if confidence < self.confidence_threshold:
                self.feedback_message += (
                    f" [Below threshold {self.confidence_threshold}]"
                )
                self.state.reply = reasoning
                return Status.FAILURE

            # Validate and set command
            if command_value:
                try:
                    self.state.command = Command(command_value)
                    return Status.SUCCESS
                except ValueError:
                    self.feedback_message = f"Invalid command value: {command_value}"
                    self.state.reply = self.feedback_message
                    return Status.FAILURE
            else:
                return Status.FAILURE

        except Exception as e:
            self.feedback_message = f"LLM command detection failed: {str(e)}"
            self.state.reply = self.feedback_message
            return Status.FAILURE


# Conditions


class IsLLMAvailable(ReleaseAction):
    def __init__(
        self, name: str, state: ConversationState, log_prefix: str = ""
    ) -> None:
        self.state = state
        super().__init__(name=name, log_prefix=log_prefix)

    def update(self) -> Status:
        if self.state.llm_available:
            return Status.SUCCESS
        return Status.FAILURE
