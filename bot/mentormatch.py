"""MentorMatch Telegram bot composed from modular handlers."""
from __future__ import annotations

from bot.core.app import BotCore
from bot.handlers.entities import EntityHandlers
from bot.handlers.identity import IdentityHandlers
from bot.handlers.matching import MatchingHandlers
from bot.handlers.menu import MenuHandlers


class MentorMatchBot(
    BotCore,
    MenuHandlers,
    IdentityHandlers,
    EntityHandlers,
    MatchingHandlers,
):
    """Concrete bot class combining core lifecycle and feature handlers."""

    pass
