from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")
DB_DIR = Path(os.getenv("DB_DIR", str(PROJECT_DIR / "data"))).expanduser()
if not DB_DIR.is_absolute():
    DB_DIR = PROJECT_DIR / DB_DIR
DB_DIR = DB_DIR.resolve()
DEFAULT_DB_PATH = DB_DIR / "baby_tracker.db"
LEGACY_DB_PATH = PROJECT_DIR / "sleep_tracker.db"

# Создатели BabyRhythm AI всегда имеют административный и Premium-доступ.
# Дополнительные администраторы можно задать в Render/.env через ADMIN_IDS.
BUILTIN_ADMIN_IDS = frozenset({303225689, 324310407})


def parse_admin_ids(raw_value: str) -> frozenset[int]:
    return frozenset(
        int(value.strip())
        for value in raw_value.split(",")
        if value.strip().isdigit() and int(value.strip()) > 0
    )


ENV_ADMIN_IDS = parse_admin_ids(os.getenv("ADMIN_IDS", ""))
ADMIN_IDS = BUILTIN_ADMIN_IDS | ENV_ADMIN_IDS


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    database_url: str
    db_path: str
    allowed_ids: frozenset[int]
    admin_ids: frozenset[int]
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


def normalize_db_path(raw_path: str, base_dir: Path | None = None) -> str:
    """Возвращает постоянный абсолютный путь SQLite, не создавая и не удаляя БД."""
    value = raw_path.strip()
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = (base_dir or DB_DIR) / path
    return str(path.resolve())


def _prepare_default_sqlite_path() -> str:
    """Создаёт каталог и бережно переносит legacy-БД без удаления оригинала."""
    _preserve_legacy_database(DEFAULT_DB_PATH)
    return str(DEFAULT_DB_PATH)


def _preserve_legacy_database(target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if (
        not target_path.exists()
        and LEGACY_DB_PATH.exists()
        and LEGACY_DB_PATH.is_file()
        and target_path.resolve() != LEGACY_DB_PATH.resolve()
    ):
        shutil.copy2(LEGACY_DB_PATH, target_path)


def _sqlite_path_from_url(database_url: str) -> str:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if database_url.startswith(prefix):
            location = database_url[len(prefix):]
            if location != ":memory:" and not location.startswith("file:"):
                return normalize_db_path(location, PROJECT_DIR)
    return ""


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
    raw_database_url = os.getenv("DATABASE_URL", "").strip()
    raw_db_path = os.getenv("DB_PATH", "").strip()
    if raw_database_url:
        database_url = normalize_database_url(raw_database_url)
        db_path = _sqlite_path_from_url(database_url)
    else:
        db_path = normalize_db_path(raw_db_path) if raw_db_path else _prepare_default_sqlite_path()
        _preserve_legacy_database(Path(db_path))
        database_url = f"sqlite+aiosqlite:///{Path(db_path).as_posix()}"

    if db_path and Path(db_path).exists() and Path(db_path).is_dir():
        raise RuntimeError(f"DB_PATH указывает на каталог, а не на файл: {db_path}")
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
        admin_ids=ADMIN_IDS,
        timezone=timezone,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        gemini_api_key=gemini_api_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip() or "gemini-3.7-flash",
    )
