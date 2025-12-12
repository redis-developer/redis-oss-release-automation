from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Command(str, Enum):

    RELEASE = "release"
    STATUS = "status"
    CUSTOM_BUILD = "custom_build"
    UNSTABLE_BUILD = "unstable_build"
    HELP = "help"


class ConversationArgs(BaseModel):
    openai_api_key: Optional[str] = None
    message: str
