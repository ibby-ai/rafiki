"""Logging configuration and utilities."""

import logging
import sys
from typing import Optional


def configure_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    include_context: bool = True,
) -> None:
    """Configure application-wide logging.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        format_string: Custom format string. If None, uses structured format.
        include_context: Whether to include contextual information in logs.
    """
    if format_string is None:
        if include_context:
            format_string = (
                "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d "
                "| %(funcName)s | %(message)s"
            )
        else:
            format_string = "%(asctime)s [%(levelname)s] %(message)s"
    
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=format_string,
        stream=sys.stdout,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module.
    
    Args:
        name: Logger name (typically __name__).
        
    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)

