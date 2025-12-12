from typing import List, Optional

from pydantic import BaseModel, Field

from ..conversation_models import Command


class InboxMessage(BaseModel):
    message: str
    context: List[str]


class ConversationState(BaseModel):
    llm_available: bool = False
    message: Optional[InboxMessage] = None
    command: Optional[Command] = None
    reply: Optional[str] = None
