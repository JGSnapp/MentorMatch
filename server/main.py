"""Entrypoint for running the MentorMatch API."""
from __future__ import annotations

from app import app as fastapi_app

app = fastapi_app

__all__ = ["app"]
