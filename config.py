from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    database_url: str
    timezone: str
    log_level: str
    gemini_api_key: str | None
    gemini_model: str


def normalize_database_url(url: str) -> str:
    """Нормализует URL драйвера и фиксирует постоянный путь локальной SQLite."""
    value = url.strip()
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value[len("postgresql://"):]
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if not value.startswith(prefix):
            continue
        location = value[len(prefix):]
        if location == ":memory:" or location.startswith("file:"):
            return value
        database_path = Path(location)
        if not database_path.is_absolute():
            database_path = PROJECT_DIR / database_path
        return prefix + database_path.resolve().as_posix()
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
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip() or "gemini-3.7-flash",
    )
