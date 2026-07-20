"""Redis version parsing and comparison."""

import functools
import re
from typing import Optional, Tuple

from pydantic import BaseModel, Field


@functools.total_ordering
class RedisVersion(BaseModel):
    """Represents a parsed Redis version."""

    major: int = Field(..., ge=1, description="Major version number")
    minor: int = Field(..., ge=0, description="Minor version number")
    patch: Optional[int] = Field(None, ge=0, description="Patch version number")
    suffix: str = Field("", description="Version suffix (e.g., -m01, -rc1, -eol)")

    @classmethod
    def parse(cls, version_str: str) -> "RedisVersion":
        """Parse a version string into components."""
        version = version_str.lstrip("v")

        match = re.match(r"^([1-9]\d*\.\d+(?:\.\d+)?)(.*)", version)
        if not match:
            raise ValueError(f"Invalid version format: {version_str}")

        numeric_part, suffix = match.groups()
        parts = numeric_part.split(".")
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else None

        return cls(major=major, minor=minor, patch=patch, suffix=suffix)

    @property
    def is_milestone(self) -> bool:
        """Check if this is a milestone version."""
        return bool(self.suffix)

    @property
    def is_eol(self) -> bool:
        """Check if this version is end-of-life."""
        return self.suffix.lower().endswith("-eol")

    @property
    def is_rc(self) -> bool:
        """Check if this version is a release candidate."""
        return self.suffix.lower().startswith("-rc")

    @property
    def is_ga(self) -> bool:
        """Check if this version is a general availability release."""
        return not self.is_milestone

    @property
    def is_internal(self) -> bool:
        """Check if this version is an internal release."""
        return bool(re.search(r"-int\d*$", self.suffix.lower()))

    @property
    def mainline_version(self) -> str:
        """Get the mainline version string."""
        return f"{self.major}.{self.minor}"

    @property
    def suffix_weight(self) -> str:
        suffix_weight = ""
        if self.is_ga:
            suffix_weight = "QQ"
        if self.is_rc:
            suffix_weight = "LL"
        elif self.suffix.startswith("-m"):
            suffix_weight = "II"

        if self.is_internal:
            suffix_weight = suffix_weight[:1] + "E"

        return suffix_weight

    @property
    def sort_key(self) -> Tuple[int, int, int, str]:
        return (
            self.major,
            self.minor,
            self.patch or 0,
            f"{self.suffix_weight}{self.suffix}",
        )

    def __str__(self) -> str:
        version = f"{self.major}.{self.minor}"
        if self.patch is not None:
            version += f".{self.patch}"
        return version + self.suffix

    def __lt__(self, other: "RedisVersion") -> bool:
        if not isinstance(other, RedisVersion):
            return NotImplemented

        return self.sort_key < other.sort_key

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RedisVersion):
            return NotImplemented

        return self.sort_key == other.sort_key

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch or 0, self.suffix))
