"""Configuration helpers for matching services."""
from __future__ import annotations

import os
from typing import Final

from dotenv import load_dotenv

load_dotenv()

PROXY_API_KEY: Final[str | None] = os.getenv("PROXY_API_KEY")
PROXY_BASE_URL: Final[str | None] = os.getenv("PROXY_BASE_URL")
PROXY_MODEL: Final[str] = os.getenv("PROXY_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE: Final[float] = float(os.getenv("MATCHING_LLM_TEMPERATURE", "0.2"))

__all__ = [
    "PROXY_API_KEY",
    "PROXY_BASE_URL",
    "PROXY_MODEL",
    "LLM_TEMPERATURE",
]
