from typing import List, Optional

from pydantic import BaseModel, Field

from ..conversation_models import Command
from ..models import ReleaseArgs, SlackArgs, SlackFormat


class InboxMessage(BaseModel):
    message: str
    context: List[str]


class ConversationState(BaseModel):
    llm_available: bool = False
    message: Optional[InboxMessage] = None
    command: Optional[Command] = None
    command_started: bool = False
    release_args: Optional[ReleaseArgs] = None
    reply: Optional[str] = None

    slack_args: Optional[SlackArgs] = None
