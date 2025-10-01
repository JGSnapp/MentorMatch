"""Application configuration helpers."""
from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv


def configure_logging(default_level: int = logging.INFO) -> int:
    """Configure root logging using the ``LOG_LEVEL`` environment variable.

    The previous implementation inside :mod:`server.main` repeated the
    configuration logic every time the module was imported. Moving the
    behaviour into a standalone function ensures that it is executed exactly
    once when creating the FastAPI application and that test suites can
    configure logging independently.
    """

    load_dotenv()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level: int = getattr(logging, level_name, default_level)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root_logger.addHandler(handler)
    root_logger.setLevel(level)
    return level


def get_bot_base_url() -> Optional[str]:
    """Return the base URL for the Telegram bot API if configured."""

    for var in ("BOT_API_URL", "BOT_INTERNAL_URL", "BOT_BASE_URL"):
        value = os.getenv(var)
        if value and value.strip():
            return value
    return "http://bot:5000"
