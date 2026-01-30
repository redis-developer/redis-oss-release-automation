from enum import Enum
from typing import List, Literal, Optional, Union

from janus import SyncQueue
from openai import OpenAI
from pydantic import BaseModel, Field

from .models import SlackArgs

IGNORE_THREAD_MESSAGE = "I will ignore this thread."


class UserIntent(str, Enum):
    QUESTION = "question"
    ACTION = "action"
    CONFIRMATION = "confirmation"
    NO_ACTION = "no_action"


INTENT_DESCRIPTIONS = {
    UserIntent.QUESTION: "The user is asking a question that needs an answer",
    UserIntent.ACTION: "The user wants to perform an action (like running a release, checking status, etc.)",
    UserIntent.CONFIRMATION: "The user is confirming or rejecting a previous action request from the bot (e.g., 'yes', 'no', 'proceed', 'cancel'). Use this when the previous bot message was a confirmation request with detected arguments.",
    UserIntent.NO_ACTION: "The user's message doesn't require any action or response (like a comment, acknowledgment, or message to another user)",
}


class Command(str, Enum):

    RELEASE = "release"
    CUSTOM_BUILD = "custom_build"
    STATUS = "status"
    HELP = "help"
    IGNORE_THREAD = "ignore_thread"
    SKIP_MESSAGE = "skip_message"


COMMAND_DESCRIPTIONS = {
    Command.RELEASE: "Start or restart a release process using provided version tag and parameters.",
    Command.CUSTOM_BUILD: """Start or restart a custom build process using provided version tag and parameters.
    Custom build allows to build Redis using arbitrary tag for redis and for all the modules.
    It is intended to run tests for custom in development versions of Redis and modules before creating actual release.
    Custom build may be referred as run tests for Redis or run tests for modules.
    """,
    Command.STATUS: "Check the status of a release: run status command for existing release state",
    Command.HELP: "Get help",
    Command.IGNORE_THREAD: "Ignore this thread, do not answer any more messages in this thread without explicit mention",
    Command.SKIP_MESSAGE: "Skip this message, it's not relevant to the conversation or is intended for other user.",
}


class InboxMessage(BaseModel):
    message: str
    user: Optional[str] = None
    is_bot: bool = False
    slack_ts: Optional[str] = None


class BotReply(BaseModel):
    """A text reply from the bot to be sent to Slack."""

    text: str


class BotReaction(BaseModel):
    """A reaction (emoji) from the bot to be added to a message."""

    emoji: str
    message_ts: Optional[str] = None  # If None, react to the inbox message


BotQueueItem = Union[BotReply, BotReaction]


class ConversationArgs(BaseModel):
    inbox: Optional[InboxMessage]
    context: Optional[List[InboxMessage]] = None
    config_path: Optional[str] = None
    slack_args: Optional[SlackArgs] = None
    openai_api_key: Optional[str] = None
    authorized_users: Optional[List[str]] = None
    emojis: List[str] = Field(default_factory=list)


class ConversationCockpit:
    llm: Optional[OpenAI] = None
    reply_queue: Optional[SyncQueue] = None


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


class LLMStatusArgs(BaseModel):
    """Simplified status arguments for LLM structured output."""

    release_tag: str = Field(description="The release tag (e.g., '8.4-m01', '7.2.5')")


class CommandDetectionResult(BaseModel):
    """Structured output for command detection."""

    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score between 0 and 1"
    )
    command: Optional[Command] = Field(
        None, description="Detected command name (release, status, custom_build, etc.)"
    )
    release_args: Optional[LLMReleaseArgs] = Field(
        None, description="Release arguments for command execution"
    )
    status_args: Optional[LLMStatusArgs] = Field(
        None, description="Status arguments for command execution"
    )
    is_confirmed: bool = Field(
        False, description="Whether the user is confirming a command"
    )
    reply: Optional[str] = Field(
        None, description="Natural language reply to send back to user"
    )


class CommandDetectionResult2(BaseModel):
    command: Optional[Command] = Field(
        None, description="Detected command name (release, status, custom_build, etc.)"
    )
    reply: Optional[str] = Field(
        None, description="Natural language reply to send back to user"
    )
    emoji: Optional[str] = Field(None, description="Emoji to react with")


class UserIntentDetectionResult(BaseModel):
    intent: Optional[UserIntent] = Field(None, description="Detected user intent")


class QuestionResolutionResult(BaseModel):
    reply: Optional[str] = Field(
        None, description="Natural language reply to send back to user"
    )
    emoji: Optional[str] = Field(None, description="Emoji to react with")


class NoActionResolutionResult(BaseModel):
    emoji: Optional[str] = Field(None, description="Emoji to react with")
