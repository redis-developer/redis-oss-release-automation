"""Wrapper to make Python's logging.Logger compatible with py_trees.logging.Logger."""

import logging

import py_trees.logging


class PyTreesLoggerWrapper(py_trees.logging.Logger):
    """Wrapper that inherits from py_trees.logging.Logger and delegates to Python's logging.Logger.

    This class inherits from py_trees.logging.Logger to satisfy type checking requirements
    while delegating all logging calls to a standard Python logging.Logger instance.
    This allows py_trees behaviours to use Python's logging infrastructure (with Rich formatting)
    while maintaining type compatibility with py_trees' expectations.

    Args:
        logger: A Python logging.Logger instance to delegate to

    Example:
        >>> import logging
        >>> from redis_release.bht import logging_wrapper
        >>>
        >>> python_logger = logging.getLogger(__name__)
        >>> wrapped_logger = logging_wrapper.Logger(python_logger)
        >>> wrapped_logger.info("[blue]Hello[/blue] [green]World[/green]")
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the logger wrapper.

        Args:
            logger: A Python logging.Logger instance to delegate to
        """
        super().__init__()
        self._logger = logger

    def debug(self, msg: str) -> None:
        """Log a message with severity 'DEBUG'.

        Args:
            msg: The message to log
        """
        self._logger.debug(msg)

    def info(self, msg: str) -> None:
        """Log a message with severity 'INFO'.

        Args:
            msg: The message to log
        """
        self._logger.info(msg)

    def warning(self, msg: str) -> None:
        """Log a message with severity 'WARNING'.

        Args:
            msg: The message to log
        """
        self._logger.warning(msg)

    def error(self, msg: str) -> None:
        """Log a message with severity 'ERROR'.

        Args:
            msg: The message to log
        """
        self._logger.error(msg)
