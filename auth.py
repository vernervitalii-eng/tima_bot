from __future__ import annotations

from aiogram import BaseMiddleware


class AllowedIdsMiddleware(BaseMiddleware):
    """Не ограничивает приложение, когда ALLOWED_IDS пуст, и фильтрует ID при настройке."""

    async def __call__(self, handler, event, data):
        settings = data.get("settings")
        allowed_ids = getattr(settings, "allowed_ids", frozenset())
        admin_ids = getattr(settings, "admin_ids", frozenset())
        sender = getattr(event, "from_user", None)
        if sender is not None and sender.id in admin_ids:
            return await handler(event, data)
        if allowed_ids and (sender is None or sender.id not in allowed_ids):
            return None
        return await handler(event, data)
