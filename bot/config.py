"""Configuration helpers for MentorMatch bot."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

import httpx
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)


def parse_positive_float(value: Any) -> Optional[float]:
    """Normalize float-like configuration values into positive floats."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            fvalue = float(value)
        except Exception:
            return None
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null"}:
            return None
        try:
            fvalue = float(stripped)
        except Exception:
            return None
    else:
        return None
    return fvalue if fvalue > 0 else None


def parse_positive_int(value: Any) -> Optional[int]:
    """Normalize identifiers that may come as str/float/0 into positive ints."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            ivalue = int(value)
        except Exception:
            return None
        return ivalue if ivalue > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null", "0"}:
            return None
        try:
            ivalue = int(stripped)
        except Exception:
            return None
        return ivalue if ivalue > 0 else None
    return None


def truthy_flag(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    try:
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return default


def create_telegram_request(env: Optional[dict[str, str]] = None) -> HTTPXRequest:
    """Build HTTPXRequest with tuned timeouts/proxy for Telegram API calls."""
    if env is None:
        env = dict(os.environ)

    def _timeout(name: str, default: float) -> Optional[float]:
        raw = env.get(name)
        if raw is None:
            return default
        parsed = parse_positive_float(raw)
        if parsed is None:
            logger.warning("Ignoring invalid %s=%r (expected positive number)", name, raw)
            return default
        return parsed

    connect_timeout = _timeout("TELEGRAM_CONNECT_TIMEOUT", 15.0)
    read_timeout = _timeout("TELEGRAM_READ_TIMEOUT", 30.0)
    write_timeout = _timeout("TELEGRAM_WRITE_TIMEOUT", 30.0)
    pool_timeout = _timeout("TELEGRAM_POOL_TIMEOUT", 5.0)

    pool_size = parse_positive_int(env.get("TELEGRAM_POOL_SIZE"))
    if not pool_size:
        pool_size = 4

    request_kwargs: dict[str, Any] = {
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "write_timeout": write_timeout,
        "pool_timeout": pool_timeout,
        "connection_pool_size": pool_size,
    }

    proxy_url = env.get("TELEGRAM_PROXY_URL") or env.get("TELEGRAM_PROXY")
    if proxy_url:
        request_kwargs["proxy"] = proxy_url
        proxy_user = env.get("TELEGRAM_PROXY_USER") or env.get("TELEGRAM_PROXY_USERNAME")
        proxy_password = env.get("TELEGRAM_PROXY_PASSWORD")
        if proxy_user or proxy_password:
            request_kwargs["proxy_auth"] = httpx.BasicAuth(proxy_user or "", proxy_password or "")

    logger.info(
        "Telegram HTTP client configured (connect=%s read=%s write=%s pool=%s, pool_size=%s%s)",
        connect_timeout,
        read_timeout,
        write_timeout,
        pool_timeout,
        pool_size,
        ", proxy enabled" if proxy_url else "",
    )
    return HTTPXRequest(**request_kwargs)


def load_admins(base_dir: Optional[Path] = None, *, candidates: Optional[Iterable[Path]] = None) -> Tuple[set[int], set[str]]:
    base = base_dir or Path(__file__).resolve().parent
    if candidates is None:
        candidates = (
            base / "admins.txt",
            base / "templates" / "admins.txt",
            base.parent / "admins.txt",
            base.parent / "templates" / "admins.txt",
            Path("/templates/admins.txt"),
        )
    admin_ids: set[int] = set()
    admin_usernames: set[str] = set()
    loaded = False
    for path in candidates:
        try:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if s.isdigit():
                        try:
                            admin_ids.add(int(s))
                        except Exception:
                            continue
                        continue
                    if s.startswith("@"):
                        s = s[1:]
                    if s.lower().startswith("https://t.me/"):
                        s = s.split("/")[-1]
                    if s:
                        admin_usernames.add(s.lower())
            loaded = True
            break
        except Exception as exc:
            logger.warning("Failed to load admins.txt from %s: %s", path, exc)
    if not loaded:
        logger.info("admins.txt not found; бот запущен без админских аккаунтов")
    return admin_ids, admin_usernames
