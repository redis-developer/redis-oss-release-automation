from enum import Enum
from typing import Dict, List, Literal, Optional, Union

from janus import SyncQueue
from openai import OpenAI
from pydantic import BaseModel, Field

from .models import RedisModule, SlackArgs

IGNORE_THREAD_MESSAGE = "I will ignore this thread."


class UserIntent(str, Enum):
    QUESTION = "question"
    ACTION = "action"
    NO_ACTION = "no_action"


INTENT_DESCRIPTIONS = {
    UserIntent.QUESTION: "The user is asking a question that needs an answer",
    UserIntent.ACTION: "The user wants to perform an action (like running a release, checking status, etc.)",
    UserIntent.NO_ACTION: "The user's message doesn't require any action or response (like a comment, acknowledgment, or message to another user)",
}


class Command(str, Enum):

    RELEASE = "release"
    STATUS = "status"
    IGNORE_THREAD = "ignore_thread"
    INSUFFICIENT_DETAILS = "insufficient_details"


COMMAND_DESCRIPTIONS = {
    Command.RELEASE: """Start or restart a  build process using provided version tag and parameters.
    Build process could be either custom build or release.
    Assume custom build unless release is explicitly mentioned.
    Custom build allows to build Redis using arbitrary tag for redis and for all the modules.
    It is intended to run tests for custom in development versions of Redis and modules before creating actual release.
    Custom build may be referred as run tests for Redis or run tests for modules.
    At least one module version mentioned in the message is a strong indicator for custom build.
    """,
    Command.STATUS: "Check the status of a release: run status command for existing release state",
    Command.IGNORE_THREAD: """This action is raised when user asks you to ignore this thread,
    to not answer any more messages, to do not reply or to stop replying.""",
    Command.INSUFFICIENT_DETAILS: "Not enough details provided to perform the action",
}

REDIS_MODULE_DESCRIPTIONS = {
    RedisModule.JSON: "RedisJSON is a module that adds JSON data type to Redis, could be referred as rejson or just json",
    RedisModule.SEARCH: "RediSearch is a module that adds search capabilities to Redis, could be referred as search module",
    RedisModule.TIMESERIES: "RedisTimeSeries is a module that adds time series capabilities to Redis, could be referred as timeseries or just ts",
    RedisModule.BLOOM: "RedisBloom is a module that adds bloom filters to Redis, could be referred as bloom filter or just bloom",
}

INSTRUCTION_SNIPPETS = {
    "bot_purpose": """The bot is intended to support release process for Redis by running builds or releases.
        Running builds implies making custom builds of Redis optionally using other than default versions of embedded modules and running tests for them.
        Running tests is primarily running tests suites of different redis clients against the build.
        That way by running client tests we ensure that there are no breaking changes or regression in redis itself or in the modules.
        Modules are always built together with redis since version 8, so it is not possible to build just a module or redis without building all the modules as well or to exclude certain module.
    """,
    "release_tags": """Release tags are either in version format or branch name format.and
    Examples: 8.4-m01, 8.6-rc1, 8.4-int3, 8.2-rc2-int1, 8.0.2
    Versions without patch number and suffix, like 8.2 or 8.6 are inicating a branch and implies custom build.
    unstable is the default redis branch, could be also referred as nightly
    git sha could also be used as release tag, that would imply custom build as well.
    Redis versions starting from 8 major versions are supported, other series cannot be built.
    """,
    "module_versions": """Module versions are either git tag names or branch names
    Note that module version COULD NOT BE A GIT SHA
    Module versions could have same names corresponding to redis branches or versions like 8.2 or 8.4.1
    """,
    "slack_format": """Please format messages using slack markup where appropriate.
    For example use code blocks for code samples, make links clickable, emphasize important words.
    Use markdown formatting for lists
    """,
}


class InboxMessage(BaseModel):
    message: str
    user: Optional[str] = None
    is_from_bot: bool = False
    # Whether this message is a mention of the bot
    is_mention: bool = False
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
    slack_format_is_available: bool = False


class ConversationCockpit:
    llm: Optional[OpenAI] = None
    reply_queue: Optional[SyncQueue] = None


class ModuleVersion(BaseModel):
    """A single module version specification."""

    module_name: str = Field(
        description="The module name (e.g., 'redisjson', 'redisearch')"
    )
    version: str = Field(description="The version tag (e.g., '2.4.0', 'v2.4.0')")


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
    custom_build: bool = Field(
        False, description="Whether this is a custom build (True) or a release (False)"
    )
    module_versions: List[ModuleVersion] = Field(
        default_factory=list,
        description="List of module versions to use (e.g., [{'module_name': 'redisjson', 'version': '2.4.0'}])",
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


class UserIntentDetectionResult(BaseModel):
    intent: UserIntent = Field(description="Detected user intent")


class QuestionResolutionResult(BaseModel):
    reply: Optional[str] = Field(
        None, description="Natural language reply to send back to user"
    )
    emoji: Optional[str] = Field(None, description="Emoji to react with")


class ActionResolutionResult(BaseModel):
    """Structured output for action resolution."""

    command: Optional[Command] = Field(
        None, description="Detected command (release, status, ignore_thread)"
    )
    release_args: Optional[LLMReleaseArgs] = Field(
        None, description="Release/build arguments for command execution"
    )
    status_args: Optional[LLMStatusArgs] = Field(
        None, description="Status arguments for status command execution"
    )
    reply: Optional[str] = Field(
        None, description="Natural language reply to send back to user"
    )
    emoji: Optional[str] = Field(None, description="Emoji to react with")


class NoActionResolutionResult(BaseModel):
    emoji: Optional[str] = Field(None, description="Emoji to react with")


class ConfirmationResult(BaseModel):
    """Structured output for confirmation detection."""

    is_confirmed: bool = Field(
        description="Whether the user is confirming (yes) or rejecting (no) the action"
    )
