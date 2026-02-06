import logging
from typing import Dict, Optional

import yaml
from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.response_input_param import ResponseInputParam

from redis_release.models import RedisModule, ReleaseArgs

from ..config import Config, custom_build_package_names
from ..conversation_models import (
    COMMAND_DESCRIPTIONS,
    CONFIRMATION_YAML_MARKER,
    REDIS_MODULE_DESCRIPTIONS,
    LLMReleaseArgs,
    LLMStatusArgs,
)
from .conversation_state import ConversationState
from .logging_wrapper import PyTreesLoggerWrapper

logger = logging.getLogger(__name__)


class LLMInputHelper:
    """Mixin class for building LLM input messages from conversation state.

    Classes using this mixin must have:
    - `state: ConversationState` attribute
    - `logger: PyTreesLoggerWrapper` attribute
    """

    state: ConversationState  # Expected to be provided by the class using this mixin
    logger: (
        PyTreesLoggerWrapper  # Expected to be provided by the class using this mixin
    )

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

    def log_reply(self, reply: Optional[str], emoji: Optional[str]) -> str:
        """Log a bot reply and/or emoji reaction.

        Args:
            reply: The reply text to log (will be trimmed to 100 characters)
            emoji: The emoji reaction to log
        """
        parts = []
        msg = ""
        if reply:
            trimmed = reply[:100] + "..." if len(reply) > 100 else reply
            parts.append(f"reply: {trimmed}")
        if emoji:
            parts.append(f"emoji: :{emoji}:")
        if parts:
            msg = f"Bot response: {', '.join(parts)}"
            self.logger.info(msg)
        return msg


class ArgsHelper:
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
            valid_packages = set(custom_build_package_names(self.config))
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

    def packages_list_with_descriptions(self, config: Config) -> str:
        """Return a formatted list of available packages with display names and descriptions.

        Args:
            config: The configuration object containing package definitions

        Returns:
            A formatted string listing packages with their display names and descriptions
        """
        lines = []
        for name, pkg in config.packages.items():
            display_name = (
                pkg.package_display_name if pkg.package_display_name else None
            )
            if display_name and pkg.description:
                line = f"- {name}: {display_name} - {pkg.description}"
            elif display_name:
                line = f"- {name}: {display_name}"
            elif pkg.description:
                line = f"- {name}: {pkg.description}"
            else:
                line = f"- {name}"

            if pkg.needs:
                line += f" (requires: {', '.join(pkg.needs)})"

            lines.append(line)
        return "\n".join(lines)

    def extract_state_name_from_context(self) -> Optional[str]:
        """Extract state name from bot messages in context.

        Looks for messages containing 'state-name: STATE_NAME' pattern.

        Returns:
            The extracted state name if found, None otherwise.
        """
        if not self.state.context:
            return None

        import re

        pattern = r"state-name:\s*(\S+)"

        for msg in reversed(self.state.context):
            if msg.is_from_bot:
                match = re.search(pattern, msg.message)
                if match:
                    return match.group(1)

        return None


class ConfirmationHelper:
    """Helper class for handling confirmation requests.

    Classes using this helper must have:
    - `state: ConversationState` attribute
    """

    state: ConversationState  # Expected to be provided by the class using this helper

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
        return "```" in prev_message and CONFIRMATION_YAML_MARKER in prev_message

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
