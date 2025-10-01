"""Utilities for normalising Telegram usernames and links."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_LINK_RE = re.compile(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)")


def normalize_telegram_link(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("@"):
        s = s[1:]
    match = _LINK_RE.search(s)
    if match:
        return f"https://t.me/{match.group(1)}"
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", s)
    return f"https://t.me/{cleaned}" if cleaned else None


def extract_tg_username(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("@"):
        s = s[1:]
    match = _LINK_RE.search(s)
    if match:
        return match.group(1)
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", s)
    return cleaned or None
