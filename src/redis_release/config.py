"""Configuration management for Redis release automation."""

from pathlib import Path
from typing import Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field

from .models import HomebrewChannel, PackageType, SnapRiskLevel

# Size for nanoid-based workflow identifiers
NANOID_SIZE = 8


class PackageConfig(BaseModel):
    """Configuration for a package type."""

    repo: str
    ref: Optional[str] = None
    package_type: PackageType
    workflow_branch: str = "autodetect"
    publish_internal_release: bool = False
    build_workflow: Union[str, bool] = Field(default=False)
    build_timeout_minutes: int = Field(default=45)
    build_inputs: Dict[str, str] = Field(default_factory=dict)
    publish_workflow: Union[str, bool] = Field(default=False)
    publish_timeout_minutes: int = Field(default=10)
    publish_inputs: Dict[str, str] = Field(default_factory=dict)
    package_display_name: Optional[str] = None
    description: Optional[str] = None
    allow_custom_build: bool = False
    needs: List[str] = Field(default_factory=list)


class Config(BaseModel):
    """Root configuration model."""

    version: int
    packages: Dict[str, PackageConfig]

    @classmethod
    def from_yaml(cls, path: Union[str, Path] = "config.yaml") -> "Config":
        """Load configuration from YAML file."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as f:
            data = yaml.safe_load(f)

        # Convert package configs to PackageConfig objects
        if "packages" in data:
            data["packages"] = {
                name: PackageConfig(**pkg_data)
                for name, pkg_data in data["packages"].items()
            }

        return cls(**data)


def load_config(path: Optional[Union[str, Path]] = None) -> Config:
    """Load configuration from YAML file.

    Args:
        path: Path to config file, defaults to config.yaml in current directory

    Returns:
        Loaded configuration object
    """
    if path is None:
        path = "config.yaml"
    return Config.from_yaml(path)


def custom_build_package_names(config: Config) -> List[str]:
    """Get packages that support custom builds."""
    return [name for name, pkg in config.packages.items() if pkg.allow_custom_build]
