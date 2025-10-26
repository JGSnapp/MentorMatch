"""Shared configuration helpers for MentorMatch services."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

load_dotenv()


def configure_logging(default_level: str = "INFO") -> int:
    """Ensure root logging is configured consistently across services."""
    level_name = (os.getenv("LOG_LEVEL") or default_level).upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root_logger.addHandler(handler)
    root_logger.setLevel(level)
    return level


configure_logging()


def build_db_dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn
    user = os.getenv("POSTGRES_USER", "mentormatch")
    password = os.getenv("POSTGRES_PASSWORD", "secret")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "mentormatch")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def get_conn():
    return psycopg2.connect(build_db_dsn())


def get_templates(directory: Optional[Path] = None) -> Jinja2Templates:
    base = directory or (Path(__file__).resolve().parent.parent / "templates")
    return Jinja2Templates(directory=str(base))


__all__ = ["configure_logging", "build_db_dsn", "get_conn", "get_templates"]
