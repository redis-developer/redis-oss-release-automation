"""Logging configuration with Rich handler for beautiful colored output."""

import logging

from rich.logging import RichHandler


def setup_logging(level: int = logging.INFO, show_path: bool = True) -> None:
    """Configure logging with Rich handler.

    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG)
        show_path: Whether to show file path and line numbers in logs

    Example:
        >>> from redis_release.logging_config import setup_logging
        >>> import logging
        >>> setup_logging(level=logging.DEBUG)
        >>> logger = logging.getLogger(__name__)
        >>> logger.info("[blue]Hello[/blue] [green]World[/green]")
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
            )
        ],
    )

    # Optionally reduce noise from some verbose libraries
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
