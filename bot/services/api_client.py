"""Async HTTP client wrapper for MentorMatch bot."""
from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class APIClient:
    """Thin wrapper around aiohttp for MentorMatch REST API calls."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def get(self, path: str, *, timeout: int = 20) -> Optional[dict[str, Any]]:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    logger.error("GET %s -> %s", url, response.status)
        except Exception as exc:
            logger.exception("GET %s failed: %s", url, exc)
        return None

    async def post(
        self,
        path: str,
        data: dict[str, Any],
        *,
        timeout: int = 60,
    ) -> Optional[dict[str, Any]]:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    if response.status == 303:
                        return {"status": "success"}
                    logger.error("POST %s -> %s", url, response.status)
        except Exception as exc:
            logger.exception("POST %s failed: %s", url, exc)
        return None
