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


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен.")
    timezone = os.getenv("BOT_TIMEZONE", "Europe/Chisinau").strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Неизвестный BOT_TIMEZONE: {timezone}") from exc
    return Settings(
        token=token,
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///sleep_tracker.db"),
        timezone=timezone,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

