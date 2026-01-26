from enum import Enum
from typing import List, Literal, Optional

from janus import SyncQueue
from openai import OpenAI
from pydantic import BaseModel, Field

from .models import SlackArgs

IGNORE_THREAD_MESSAGE = "I will ignore this thread."


class Command(str, Enum):

    RELEASE = "release"
    CUSTOM_BUILD = "custom_build"
    STATUS = "status"
    HELP = "help"
    IGNORE_THREAD = "ignore_thread"
    NEED_CONTEXT = "need_context"


COMMAND_DESCRIPTIONS = {
    Command.RELEASE: "Start or restart a release process using provided version tag and parameters.",
    Command.CUSTOM_BUILD: "Start or restart a custom build process using provided version tag and parameters. Custom build allows to build Redis using arbitrary tag for redis and for all the modules",
    Command.STATUS: "Check the status of a release: run status command for existing release state",
    Command.HELP: "Get help",
    Command.IGNORE_THREAD: "Ignore this thread, do not answer any more messages in this thread without explicit mention",
    Command.NEED_CONTEXT: "Need more context to understand the request",
}


class InboxMessage(BaseModel):
    message: str
    context: List[str]
    user: Optional[str] = None


class ConversationArgs(BaseModel):
    inbox: Optional[InboxMessage]
    config_path: Optional[str] = None
    slack_args: Optional[SlackArgs] = None
    openai_api_key: Optional[str] = None
    authorized_users: Optional[List[str]] = None


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
