"""Core application setup for MentorMatch Telegram bot."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from aiohttp import web
from dotenv import load_dotenv
from telegram.ext import Application

from bot import dispatcher
from bot.config import (
    create_telegram_request,
    load_admins,
    parse_positive_float,
    parse_positive_int,
    truthy_flag,
)
from bot.services.api_client import APIClient

logger = logging.getLogger(__name__)


class BotCore:
    EDIT_KEEP = "__keep__"

    def __init__(self) -> None:
        load_dotenv()

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан в окружении")

        self.server_url = os.getenv("SERVER_URL", "http://localhost:8000")
        self.admin_ids, self.admin_usernames = load_admins()

        self.http_host = os.getenv("BOT_HTTP_HOST", "0.0.0.0")
        port_raw = os.getenv("BOT_HTTP_PORT", "5000")
        port = parse_positive_int(port_raw)
        self.http_port = port or 5000

        self._http_app = web.Application()
        self._http_app.add_routes(
            [
                web.get("/healthz", self._handle_healthcheck),
                web.post("/notify", self._handle_notify),
            ]
        )
        self._http_runner: Optional[web.AppRunner] = None
        self._http_site: Optional[web.BaseSite] = None

        request = create_telegram_request()
        self._telegram_request = request
        self.app = Application.builder().token(token).request(request).build()
        self.app.post_init = self._post_init
        self.app.post_shutdown = self._post_shutdown

        self.api = APIClient(self.server_url)

        dispatcher.setup(self.app, self)

    # --- helper wrappers to keep backwards compatibility ---
    def _parse_positive_float(self, value: Any) -> Optional[float]:
        return parse_positive_float(value)

    def _parse_positive_int(self, value: Any) -> Optional[int]:
        return parse_positive_int(value)

    def _truthy_flag(self, value: Any, *, default: bool = False) -> bool:
        return truthy_flag(value, default=default)

    # Lifecycle -----------------------------------------------------------
    async def _post_init(self, _: Application) -> None:
        try:
            await self._start_http_server()
        except Exception:
            logger.exception("Не удалось запустить внутренний HTTP-сервер уведомлений")

    async def _post_shutdown(self, _: Application) -> None:
        try:
            await self._stop_http_server()
        except Exception:
            logger.exception("Ошибка при остановке внутреннего HTTP-сервера уведомлений")

    async def _start_http_server(self) -> None:
        if self._http_runner is not None:
            return
        self._http_runner = web.AppRunner(self._http_app)
        await self._http_runner.setup()
        self._http_site = web.TCPSite(
            self._http_runner, host=self.http_host, port=self.http_port
        )
        await self._http_site.start()
        logger.info("Bot HTTP API listening on %s:%s", self.http_host, self.http_port)

    async def _stop_http_server(self) -> None:
        if self._http_site is not None:
            await self._http_site.stop()
            self._http_site = None
        if self._http_runner is not None:
            await self._http_runner.cleanup()
            self._http_runner = None
            logger.info("Bot HTTP API stopped")

    async def _handle_healthcheck(self, _: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_notify(self, request: web.Request) -> web.Response:
        payload: dict[str, Any] = {}
        if request.can_read_body:
            try:
                if request.content_type and "json" in request.content_type:
                    payload = await request.json()
                else:
                    payload = dict(await request.post())
            except Exception as exc:
                logger.warning("Failed to parse notify payload: %s", exc)
                payload = {}
        if not payload:
            payload = dict(request.query)
        chat_id_raw = payload.get("chat_id") or payload.get("telegram_id")
        chat_id = self._parse_positive_int(chat_id_raw)
        if chat_id is None:
            return web.json_response(
                {"status": "error", "message": "chat_id is required"}, status=400
            )
        text_val = payload.get("text")
        if text_val is None:
            return web.json_response(
                {"status": "error", "message": "text is required"}, status=400
            )
        text_raw = text_val if isinstance(text_val, str) else str(text_val)
        if not str(text_raw).strip():
            return web.json_response(
                {"status": "error", "message": "text is required"}, status=400
            )
        reply_markup = self._build_reply_markup(payload)
        disable_preview = self._truthy_flag(
            payload.get("disable_web_page_preview"), default=True
        )
        parse_mode = payload.get("parse_mode")
        message_kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": self._fix_text(text_raw),
            "disable_web_page_preview": disable_preview,
        }
        if reply_markup is not None:
            message_kwargs["reply_markup"] = reply_markup
        if parse_mode:
            message_kwargs["parse_mode"] = str(parse_mode)
        try:
            await self.app.bot.send_message(**message_kwargs)
        except Exception as exc:
            logger.warning("Failed to send notification to %s: %s", chat_id, exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=502)
        return web.json_response({"status": "ok"})

    def run(self) -> None:
        self.app.run_polling()

    # API wrappers -------------------------------------------------------
    async def _api_get(self, path: str) -> Optional[dict[str, Any]]:
        return await self.api.get(path)

    async def _api_post(
        self, path: str, data: dict[str, Any], timeout: int = 60
    ) -> Optional[dict[str, Any]]:
        return await self.api.post(path, data, timeout=timeout)

    # Placeholders for mixins -------------------------------------------
    def _build_reply_markup(self, payload: dict[str, Any]):  # pragma: no cover - overridden
        handler = getattr(super(), "_build_reply_markup", None)
        if handler is None:
            return None
        return handler(payload)

    def _fix_text(self, s: Any):  # pragma: no cover - overridden
        handler = getattr(super(), "_fix_text", None)
        if handler is None:
            return s
        return handler(s)
