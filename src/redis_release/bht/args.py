"""Arguments for release automation."""

from typing import List

from pydantic import BaseModel, Field


class ReleaseArgs(BaseModel):
    """Arguments for release execution."""

    release_tag: str
    force_rebuild: List[str] = Field(default_factory=list)
    only_packages: List[str] = Field(default_factory=list)
