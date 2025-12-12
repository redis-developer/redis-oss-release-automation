from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from .models import SlackArgs


class Command(str, Enum):

    RELEASE = "release"
    STATUS = "status"
    CUSTOM_BUILD = "custom_build"
    UNSTABLE_BUILD = "unstable_build"
    HELP = "help"


class ConversationArgs(BaseModel):
    openai_api_key: Optional[str] = None
    message: str
    context: Optional[List[str]] = None
    config_path: Optional[str] = None

    slack_args: Optional[SlackArgs] = None
