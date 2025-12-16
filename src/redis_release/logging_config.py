"""Logging configuration with Rich handler for beautiful colored output."""

import logging
import os
from typing import Dict, Optional

from rich.logging import RichHandler


def _get_log_level_from_env() -> Optional[int]:
    """Get log level from environment variables.

    Checks for LOG_LEVEL and LOGGING_LEVEL environment variables.
    Supports both numeric values (10, 20, 30, 40, 50) and string values
    (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Log level as integer, or None if not found/invalid
    """
    for env_var in ["LOG_LEVEL", "LOGGING_LEVEL"]:
        level_str = os.getenv(env_var)
        if level_str:
            # Try to parse as integer first
            try:
                return int(level_str)
            except ValueError:
                pass

            # Try to parse as string level name
            level_str = level_str.upper()
            level_map = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "WARN": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL,
                "FATAL": logging.CRITICAL,
            }
            if level_str in level_map:
                return level_map[level_str]

    return None


def setup_logging(
    level: Optional[int] = None,
    show_path: bool = True,
    third_party_level: int = logging.WARNING,
    log_file: Optional[str] = None,
) -> None:
    """Configure logging with Rich handler for beautiful colored output.

    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG).
               If None, will check LOG_LEVEL or LOGGING_LEVEL environment variables.
               Defaults to logging.INFO if no environment variable is set.
        show_path: Whether to show file path and line numbers in logs
        third_party_level: Logging level for third-party libraries (botocore, boto3, etc.)
        log_file: Optional file path to also log to a file
    """
    # Determine the actual log level to use
    if level is None:
        level = _get_log_level_from_env()
        if level is None:
            level = logging.INFO

    handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_level=True,
        show_path=show_path,
        markup=True,
        tracebacks_show_locals=True,
        omit_repeated_times=False,
    )

    handlers = [handler]

    # Add file handler if log_file is specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(name)s: %(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )

    # Set root logger to the desired level
    logging.getLogger().setLevel(level)

    # Optionally reduce noise from some verbose libraries
    logging.getLogger("asyncio").setLevel(third_party_level)
    logging.getLogger("aiohttp").setLevel(third_party_level)
    logging.getLogger("botocore").setLevel(third_party_level)
    logging.getLogger("boto3").setLevel(third_party_level)
    logging.getLogger("urllib3").setLevel(third_party_level)


def log_once(key: str, container: Dict[str, bool]) -> bool:
    if key not in container:
        container[key] = True
        return True
    return False
