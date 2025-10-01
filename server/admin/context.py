from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from fastapi.templating import Jinja2Templates


@dataclass
class AdminContext:
    get_conn: Callable[[], Any]
    templates: Jinja2Templates
