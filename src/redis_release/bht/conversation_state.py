from typing import List, Optional

from pydantic import BaseModel, Field

from ..conversation_models import Command, InboxMessage, LLMReleaseArgs
from ..models import ReleaseArgs, SlackArgs, SlackFormat


class ConversationState(BaseModel):
    llm_available: bool = False
    llm_confirmation_required: bool = True
    message: Optional[InboxMessage] = None
    context: Optional[List[InboxMessage]] = None
    command: Optional[Command] = None
    command_started: bool = False
    is_confirmed: bool = False
    llm_release_args: Optional[LLMReleaseArgs] = None
    release_args: Optional[ReleaseArgs] = None
    replies: List[str] = Field(default_factory=list)

    slack_args: Optional[SlackArgs] = None
    authorized_users: Optional[List[str]] = None
