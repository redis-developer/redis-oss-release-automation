"""Configuration management for Redis release automation."""

from pathlib import Path
from typing import Dict, Union

import yaml
from pydantic import BaseModel, Field


class PackageConfig(BaseModel):
    """Configuration for a package type."""

    repo: str
    workflow_branch: str = "autodetect"
    build_workflow: Union[str, bool] = Field(default=False)
    build_timeout_minutes: int = Field(default=45)
    publish_workflow: Union[str, bool] = Field(default=False)
    publish_timeout_minutes: int = Field(default=10)


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


def load_config(path: Union[str, Path] = "config.yaml") -> Config:
    """Load configuration from YAML file.

    Args:
        path: Path to config file, defaults to config.yaml in current directory

    Returns:
        Loaded configuration object
    """
    return Config.from_yaml(path)
