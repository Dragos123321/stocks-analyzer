"""
Central logging configuration for the stock-analyzer backend.

Configure once at startup (e.g. when any FastAPI app is imported).
Logs go to a file only (no console). Log level from env LOG_LEVEL (default INFO).
Log file path from env LOG_FILE (default log.txt).
"""
from __future__ import annotations

import logging
import os


def configure_logging(
    level: str | int | None = None,
    format_string: str | None = None,
    log_file: str | None = None,
) -> None:
    """
    Configure the root logger for the application. Logs are written to a file only.

    Args:
        level: Log level (e.g. 'DEBUG', 'INFO'). Defaults to env LOG_LEVEL or INFO.
        format_string: Custom format. Default: timestamp, level, name, message.
        log_file: Path to log file. Defaults to env LOG_FILE or "log.txt".
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if isinstance(level, str):
        level = getattr(logging, level, logging.INFO)

    if format_string is None:
        format_string = (
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

    if log_file is None:
        log_file = os.environ.get("LOG_FILE", "log.txt")

    logging.basicConfig(
        level=level,
        format=format_string,
        datefmt="%Y-%m-%d %H:%M:%S",
        filename=log_file,
        encoding="utf-8",
        force=True,
    )
    # Reduce noise from third-party libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name (typically __name__)."""
    return logging.getLogger(name)
