from enum import Enum
from typing import List, Optional

from janus import SyncQueue
from openai import OpenAI
from pydantic import BaseModel

from .models import SlackArgs


class Command(str, Enum):

    RELEASE = "release"
    STATUS = "status"
    CUSTOM_BUILD = "custom_build"
    UNSTABLE_BUILD = "unstable_build"
    HELP = "help"


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
