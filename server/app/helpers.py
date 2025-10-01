"""Miscellaneous helper utilities."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .notifications import shorten


def truthy(val: Optional[str]) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    import csv

    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def is_http_url(value: Optional[str]) -> bool:
    return bool(value) and str(value).strip().lower().startswith(("http://", "https://"))


__all__ = ["truthy", "read_csv_rows", "is_http_url", "shorten"]
