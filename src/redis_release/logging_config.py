"""Logging configuration with Rich handler for beautiful colored output."""

import logging
import os
import threading
from typing import Dict, Optional

from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.text import Text

_log_context = threading.local()

_PREFIX_COLORS = (
    "cyan",
    "magenta",
    "yellow",
    "green",
    "blue",
    "bright_cyan",
    "bright_magenta",
    "bright_yellow",
    "bright_green",
    "bright_blue",
)


def get_log_prefix() -> str:
    """Get the current thread's log prefix."""
    return getattr(_log_context, "prefix", "")


def set_log_prefix(prefix: str) -> None:
    """Set the log prefix for the current thread."""
    _log_context.prefix = prefix


_prefix_color_lock = threading.Lock()
_prefix_color_map: Dict[str, str] = {}


def _color_for_prefix(prefix: str) -> str:
    # First-seen assignment: each new prefix gets the next colour in the
    # palette. This avoids hash collisions making two distinct prefixes
    # share a colour until the palette is exhausted.
    with _prefix_color_lock:
        cached = _prefix_color_map.get(prefix)
        if cached is not None:
            return cached
        color = _PREFIX_COLORS[len(_prefix_color_map) % len(_PREFIX_COLORS)]
        _prefix_color_map[prefix] = color
        return color


class PrefixFilter(logging.Filter):
    """Filter that prepends a thread-local prefix to logger name.

    The prefix is wrapped in Rich color markup so that, when rendered by
    RichHandler (markup=True), each distinct prefix gets a stable color.
    PlainTextFormatter strips the markup before writing to file handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_prefix_applied", False):
            return True

        # Two sources of "prefix":
        #   1. Thread-local prefix set via set_log_prefix (slack conversation
        #      flow). Rendered as a leading "[prefix]" block.
        #   2. The first dot-separated segment of the logger name itself.
        #      LoggingAction bakes a package_name into the logger name (e.g.
        #      "redis-py.IdentifyTargetRef"), so colouring that segment gives
        #      a stable per-package colour without changing call sites.
        # \[ escapes the literal opening bracket so Rich doesn't try to
        # interpret it as a markup tag. The closing ] is fine as-is.
        thread_prefix = get_log_prefix()
        if thread_prefix:
            color = _color_for_prefix(thread_prefix)
            record.name = f"[bold {color}]\\[{thread_prefix}][/] {record.name}"

        first_dot = record.name.find(".")
        if first_dot > 0:
            segment = record.name[:first_dot]
            color = _color_for_prefix(segment)
            record.name = f"[bold {color}]{segment}[/]{record.name[first_dot:]}"

        record._prefix_applied = True
        return True


class PlainTextFormatter(logging.Formatter):
    """Formatter that strips Rich markup from log messages."""

    def format(self, record: logging.LogRecord) -> str:
        # Strip Rich markup from the message
        if record.msg:
            try:
                # Parse Rich markup and extract plain text
                text = Text.from_markup(str(record.msg))
                record.msg = text.plain
            except Exception:
                # If parsing fails, use the message as-is
                pass

        # Strip Rich markup from the logger name (PrefixFilter writes color
        # tags into it for the console handler).
        if record.name:
            try:
                record.name = Text.from_markup(record.name).plain
            except Exception:
                pass

        # Format any arguments if present
        if record.args:
            try:
                record.msg = record.msg % record.args
                record.args = None
            except Exception:
                pass

        # Add basename to record for use in format string
        record.basename = os.path.basename(record.pathname)

        return super().format(record)


def _parse_log_level(level_str: str) -> int:
    """Parse log level from string.

    Supports: DEBUG, INFO, WARNING, ERROR, CRITICAL (case-insensitive).

    Args:
        level_str: Log level as string

    Returns:
        Log level as integer

    Raises:
        ValueError: If the log level string is invalid
    """
    level_str_upper = level_str.upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
        "FATAL": logging.CRITICAL,
    }
    if level_str_upper in level_map:
        return level_map[level_str_upper]

    raise ValueError(
        f"Invalid log level: '{level_str}'. "
        f"Supported values: debug, info, warning, error, critical"
    )


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
            return _parse_log_level(level_str)

    return None


def setup_logging(
    level: Optional[int] = None,
    show_path: bool = True,
    third_party_level: int = logging.WARNING,
    log_file: Optional[str] = None,
    log_file_level: Optional[str] = None,
) -> None:
    """Configure logging with Rich handler for beautiful colored output.

    Args:
        level: Logging level for console output (e.g., logging.INFO, logging.DEBUG).
               If None, will check LOG_LEVEL or LOGGING_LEVEL environment variables.
               Defaults to logging.INFO if no environment variable is set.
        show_path: Whether to show file path and line numbers in logs
        third_party_level: Logging level for third-party libraries (botocore, boto3, etc.)
        log_file: Optional file path to also log to a file.
                  If None, will check LOG_FILE environment variable.
        log_file_level: Logging level for file output as string (e.g., "debug", "info").
                        If None, will check LOG_FILE_LEVEL environment variable.
                        Defaults to "debug" if no environment variable is set.

    Raises:
        ValueError: If log_file_level is an invalid string
    """
    # Determine the actual log level to use for console
    if level is None:
        level = _get_log_level_from_env()
        if level is None:
            level = logging.INFO

    # Check for log file from environment if not provided
    if log_file is None:
        log_file = os.getenv("LOG_FILE")

    # Determine the log file level
    file_level_int: int
    if log_file_level is None:
        log_file_level_str = os.getenv("LOG_FILE_LEVEL")
        if log_file_level_str:
            file_level_int = _parse_log_level(log_file_level_str)
        else:
            file_level_int = logging.DEBUG
    else:
        file_level_int = _parse_log_level(log_file_level)

    # Set root logger to the minimum of console and file levels
    # This ensures both handlers can receive messages at their respective levels
    min_level = min(level, file_level_int) if log_file else level

    # Add Rich handler for console output.
    # NullHighlighter disables Rich's default repr-highlighter so it doesn't
    # recolor numbers/strings inside the message and override the per-prefix
    # color applied by PrefixFilter.
    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_level=True,
        show_path=show_path,
        markup=True,
        tracebacks_show_locals=True,
        omit_repeated_times=False,
        highlighter=NullHighlighter(),
    )
    rich_handler.setLevel(level)

    # Add prefix filter to prepend thread-local prefix to all log messages.
    # The filter writes Rich markup into the name; RichHandler renders the
    # colors, and PlainTextFormatter strips the markup for the file handler.
    prefix_filter = PrefixFilter()
    rich_handler.addFilter(prefix_filter)

    handlers: list = [rich_handler]

    # Add file handler if log_file is specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(file_level_int)
        file_handler.addFilter(prefix_filter)
        # Use PlainTextFormatter to strip Rich markup
        # Format: timestamp\tlogger_name\tlevel\tmessage\tfilename:line
        formatter = PlainTextFormatter(
            "%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s\t%(basename)s:%(lineno)d"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    # Configure basic logging with both handlers
    # The format here is used by RichHandler to show logger name
    logging.basicConfig(
        level=min_level,
        format="%(name)s: %(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )

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
