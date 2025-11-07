"""
Logging configuration utilities for application-level code.

This module provides helpers to configure loguru for application logging by:
- Intercepting and forwarding Python standard library logging to loguru.
- Setting up consistent log formatting and output destinations.

For use in application entry points, not libraries:
https://loguru.readthedocs.io/en/stable/overview.html#suitable-for-scripts-and-libraries
"""

import inspect
import logging
import sys
from typing import Optional, Union

from loguru import logger

DEFAULT_LOG_FORMAT = "<y>{level:<7}</y>|{process.id:>8}|{elapsed}|{time:YYYY-MM-DD HH:mm:ssZ!UTC}|{name}|{function}|{message}"

LOGURU_LEVEL_NAMES = [
    "TRACE",
    "DEBUG",
    "INFO",
    "SUCCESS",
    "WARNING",
    "ERROR",
    "CRITICAL",
]


class InterceptHandler(logging.Handler):
    """
    Handler to intercept standard library logging and redirect to loguru.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """
        Processes log records by forwarding them to loguru. Maps standard library log levels to loguru levels and
        preserves the original call stack information for accurate source tracking.

        @param record: The logging.LogRecord to be processed
        @return: None
        """
        # Get the corresponding Loguru level if it exists
        level: Union[str, int]
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def logging_config(
    log_format: str = DEFAULT_LOG_FORMAT,
    log_level: str = "INFO",
    log_file: Optional[str] = None,
) -> logger:
    """
    Configures loguru for application logging.

    @param log_format: Format string for log messages.
    @param log_level: Minimum level to log.
    @param log_file: Optional file path to write logs.
    @return: Configured logger instance.
    """
    logger.remove()  # Remove default handlers

    # Configure stderr as the primary log sink
    log_config = {
        "handlers": [
            {
                "sink": sys.stderr,
                "colorize": True,
                "format": log_format,
                "level": log_level,
            },
        ]
    }

    # Add file logging if specified
    if log_file:
        log_config["handlers"].append(
            {"sink": log_file, "format": log_format, "level": log_level},
        )

    # Apply configuration
    logger.configure(**log_config)

    # Check if standard library logging is already intercepted
    if not any(isinstance(h, InterceptHandler) for h in logging.getLogger().handlers):
        # Intercept standard library logging if it is not already intercepted
        logging.basicConfig(handlers=[InterceptHandler()], force=True)
        logger.info(f"logging package root handlers: {logging.getLogger().handlers}")

    return logger
