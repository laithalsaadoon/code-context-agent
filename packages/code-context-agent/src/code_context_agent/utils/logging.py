"""Common logging configuration using loguru."""

from __future__ import annotations

import sys

from loguru import Logger, logger


def setup_logger(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure loguru logger with standard format.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional file path for logging output
    """
    # Remove default handler
    logger.remove()

    # Add console handler with custom format
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
        level=level,
        colorize=True,
    )

    # Add file handler if specified
    if log_file:
        logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level=level,
            rotation="10 MB",
            retention="7 days",
        )


def get_logger(name: str) -> Logger:
    """Get a logger instance bound to a specific module name.

    Args:
        name: Module name (typically __name__)

    Returns:
        Bound logger instance
    """
    return logger.bind(name=name)
