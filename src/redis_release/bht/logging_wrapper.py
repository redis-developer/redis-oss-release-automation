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
        """Log a message with severity 'DEBUG'."""
        self._logger.debug(msg, stacklevel=2)

    def info(self, msg: str) -> None:
        """Log a message with severity 'INFO'."""
        self._logger.info(msg, stacklevel=2)

    def warning(self, msg: str) -> None:
        """Log a message with severity 'WARNING'."""
        self._logger.warning(msg, stacklevel=2)

    def error(self, msg: str) -> None:
        """Log a message with severity 'ERROR'."""
        self._logger.error(msg, stacklevel=2)

    def with_prefix(self, prefix: str) -> "PyTreesLoggerWrapper":
        """Create a child logger with an additional prefix.

        Args:
            prefix: Additional prefix to append to the logger name

        Returns:
            A new PyTreesLoggerWrapper with the extended name
        """
        child_name = f"{self._logger.name}.{prefix}"
        return PyTreesLoggerWrapper(logging.getLogger(child_name))
