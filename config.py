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
    db_path: str
    allowed_ids: frozenset[int]
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


def normalize_db_path(raw_path: str) -> str:
    """Возвращает постоянный абсолютный путь SQLite, не создавая и не удаляя БД."""
    value = raw_path.strip()
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return str(path.resolve())


def parse_allowed_ids(raw_value: str) -> frozenset[int]:
    """Разбирает ALLOWED_IDS; пустое значение сохраняет прежний режим без фильтра."""
    values = [item.strip() for item in raw_value.replace(";", ",").split(",") if item.strip()]
    parsed: set[int] = set()
    for value in values:
        if not value.isdigit() or int(value) <= 0:
            raise RuntimeError("ALLOWED_IDS должен содержать положительные Telegram ID через запятую.")
        parsed.add(int(value))
    return frozenset(parsed)


def load_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_BOTFATHER_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен.")
    if ":" not in token or not token.split(":", 1)[0].isdigit():
        raise RuntimeError("BOT_TOKEN имеет неверный формат. Нужен API Token от @BotFather, а не Telegram ID.")
    raw_database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///sleep_tracker.db")
    db_path = normalize_db_path(os.getenv("DB_PATH", ""))
    if db_path and (Path(db_path).exists() and Path(db_path).is_dir()):
        raise RuntimeError(f"DB_PATH указывает на каталог, а не на файл: {db_path}")
    if db_path and (raw_database_url.strip().startswith("sqlite") or not raw_database_url.strip()):
        database_url = f"sqlite+aiosqlite:///{Path(db_path).as_posix()}"
    else:
        database_url = normalize_database_url(raw_database_url)
    if not db_path and database_url.startswith("sqlite"):
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if database_url.startswith(prefix):
                location = database_url[len(prefix):]
                if location not in {":memory:"} and not location.startswith("file:"):
                    db_path = normalize_db_path(location)
                break
    if db_path and database_url.startswith("sqlite"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip() or None
    if gemini_api_key and (not gemini_api_key.startswith("AIza") or len(gemini_api_key) < 20):
        raise RuntimeError("GEMINI_API_KEY имеет неверный формат ключа Google AI Studio.")
    timezone = os.getenv("BOT_TIMEZONE", "Europe/Chisinau").strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Неизвестный BOT_TIMEZONE: {timezone}") from exc
    return Settings(
        token=token,
        database_url=database_url,
        db_path=db_path,
        allowed_ids=parse_allowed_ids(os.getenv("ALLOWED_IDS", "")),
        timezone=timezone,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        gemini_api_key=gemini_api_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip() or "gemini-3.7-flash",
    )
