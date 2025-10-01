from __future__ import annotations

import hashlib
import math
import os
import re
from typing import List, Optional, Sequence

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


def normalize_text_parts(parts: Sequence[Optional[str]], *, limit: int = 12000) -> str:
    cleaned: List[str] = []
    for part in parts:
        if not part:
            continue
        text = str(part).strip()
        if not text:
            continue
        cleaned.append(text)
    if not cleaned:
        return ""
    joined = "\n".join(cleaned)
    return joined[:limit]


def _tokenize(text: str) -> List[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _hash_token(token: str) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    # Use first 8 bytes for deterministic but compact hash, then map to feature space
    return int.from_bytes(digest[:8], "big") % EMBEDDING_DIM


def _build_vector(tokens: Sequence[str]) -> Optional[List[float]]:
    if not tokens:
        return None
    counts: dict[int, float] = {}
    total = float(len(tokens))
    for token in tokens:
        idx = _hash_token(token)
        counts[idx] = counts.get(idx, 0.0) + 1.0
    if not counts:
        return None
    vector = [0.0] * EMBEDDING_DIM
    squared_sum = 0.0
    for idx, count in counts.items():
        tf = count / total
        vector[idx] = tf
        squared_sum += tf * tf
    if squared_sum <= 0.0:
        return None
    norm = math.sqrt(squared_sum)
    scale = 1.0 / norm
    return [value * scale for value in vector]


def compute_embedding_from_text(text: str) -> Optional[List[float]]:
    stripped = (text or "").strip()
    if not stripped:
        return None
    tokens = _tokenize(stripped)
    return _build_vector(tokens)


def compute_embedding_from_parts(parts: Sequence[Optional[str]]) -> Optional[List[float]]:
    text = normalize_text_parts(parts)
    if not text:
        return None
    return compute_embedding_from_text(text)


__all__ = [
    "EMBEDDING_DIM",
    "normalize_text_parts",
    "compute_embedding_from_text",
    "compute_embedding_from_parts",
]
