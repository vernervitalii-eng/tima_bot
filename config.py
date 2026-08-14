from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    database_url: str
    timezone: str
    log_level: str


def normalize_database_url(url: str) -> str:
    """Преобразует обычный URL Render в async URL SQLAlchemy."""
    value = url.strip()
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value[len("postgresql://"):]
    return value


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_BOTFATHER_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен.")
    if ":" not in token or not token.split(":", 1)[0].isdigit():
        raise RuntimeError("BOT_TOKEN имеет неверный формат. Нужен API Token от @BotFather, а не Telegram ID.")
    timezone = os.getenv("BOT_TIMEZONE", "Europe/Chisinau").strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Неизвестный BOT_TIMEZONE: {timezone}") from exc
    return Settings(
        token=token,
        database_url=normalize_database_url(
            os.getenv("DATABASE_URL", "sqlite+aiosqlite:///sleep_tracker.db")
        ),
        timezone=timezone,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
