import time
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from cachetools import TTLCache

logger = logging.getLogger(__name__)

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(
        self,
        default_rate: float = 0.5,
        action_rates: Dict[str, float] | None = None,
    ):
        self.default_rate = default_rate
        self.action_rates = action_rates or {}
        self._last = TTLCache(maxsize=50_000, ttl=300)
        self._warned_at = TTLCache(maxsize=50_000, ttl=60)
        self._flood = TTLCache(maxsize=50_000, ttl=60)

    @staticmethod
    def _resolve_action(event: TelegramObject) -> str:
        if isinstance(event, Message):
            text = getattr(event, "text", None) or ""
            if text.startswith("/start"):
                return "start"
            if event.photo:
                return "photo_receipt"
            return "message"

        if isinstance(event, CallbackQuery):
            data = getattr(event, "data", None) or ""
            if data == "buy_sub":
                return "callback_buy"
            if data.startswith(("approve_", "reject_")):
                return "callback_admin"
            return "callback"

        return "default"

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        # Защита от флуда: если более 10 событий в минуту — дроп
        flood_count = self._flood.get(user.id, 0) + 1
        self._flood[user.id] = flood_count
        if flood_count > 10:
            logger.debug(f"[ DEBUG ] Cброшен флудящий UID = {user.id}.")
            return None

        action = self._resolve_action(event)
        rate = self.action_rates.get(action, self.default_rate)
        now = time.monotonic()

        cache_key = (user.id, action)
        last = self._last.get(cache_key, 0.0)
        if now - last < rate:
            last_warn = self._warned_at.get(user.id, 0.0)
            if now - last_warn > 5.0:
                self._warned_at[user.id] = now
                try:
                    if isinstance(event, Message):
                        await event.answer(
                            "<b>root@fsociety:~#</b> <code>err: rate_limit exceeded</code>\n\n"
                            "Too fast, Friend."
                        )
                    elif isinstance(event, CallbackQuery):
                        await event.answer(
                            "Slow down.", show_alert=False
                        )
                except Exception:
                    pass
            logger.debug(f"[ DEBUG ] Троттлинг UID = {user.id}, действие = {action}.")
            return None

        self._last[cache_key] = now
        return await handler(event, data)
