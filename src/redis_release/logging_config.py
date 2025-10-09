"""Logging configuration with Rich handler for beautiful colored output."""

import logging

from rich.logging import RichHandler


def setup_logging(
    level: int = logging.INFO,
    show_path: bool = True,
    third_party_level: int = logging.WARNING,
) -> None:
    """Configure logging with Rich handler.

    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG)
        show_path: Whether to show file path and line numbers in logs
        third_party_level: Logging level for third-party libraries (botocore, boto3, etc.)

    Example:
        >>> from redis_release.logging_config import setup_logging
        >>> import logging
        >>> setup_logging(level=logging.DEBUG)
        >>> logger = logging.getLogger(__name__)
        >>> logger.info("[blue]Hello[/blue] [green]World[/green]")

        # To see botocore debug logs:
        >>> setup_logging(level=logging.DEBUG, third_party_level=logging.DEBUG)
    """
    logging.basicConfig(
        level=level,
        format="[cyan1]%(name)s:[/cyan1] %(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                rich_tracebacks=True,
                show_time=True,
                show_level=True,
                show_path=show_path,
                markup=True,  # Enable Rich markup in log messages
                tracebacks_show_locals=True,  # Show local variables in tracebacks
                omit_repeated_times=False,  # Force timestamp on every line
            )
        ],
    )

    # Optionally reduce noise from some verbose libraries
    logging.getLogger("asyncio").setLevel(third_party_level)
    logging.getLogger("aiohttp").setLevel(third_party_level)
    logging.getLogger("botocore").setLevel(third_party_level)
    logging.getLogger("boto3").setLevel(third_party_level)
    logging.getLogger("urllib3").setLevel(third_party_level)
