from datetime import date, datetime

from services.norms import norm_for_age
from services.time_utils import age_parts, is_quiet_hours, parse_relative_time
from keyboards.inline import family_keyboard, settings_keyboard, start_choice_keyboard
from config import PROJECT_DIR, normalize_database_url
from database.models import SleepLog
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.dialects import postgresql, sqlite


def test_norm_boundaries():
    assert norm_for_age(5).wake_max == 135
    assert norm_for_age(24).sleep_min == 12


def test_age_parts():
    assert age_parts(date(2025, 1, 15), date(2025, 3, 20)) == (2, 5)


def test_parser_minutes_ago():
    now = datetime(2025, 3, 20, 12, 0)
    assert parse_relative_time("проснулся 20 минут назад", "UTC", now) == datetime(2025, 3, 20, 11, 40)


def test_parser_clock_rolls_to_previous_day():
    now = datetime(2025, 3, 20, 1, 0)
    assert parse_relative_time("уснул в 23:30", "UTC", now) == datetime(2025, 3, 19, 23, 30)


def test_quiet_hours():
    assert is_quiet_hours("UTC", datetime(2025, 3, 20, 23, 0))
    assert not is_quiet_hours("UTC", datetime(2025, 3, 20, 12, 0))


def test_start_and_reset_keyboards():
    start_callbacks = [button.callback_data for row in start_choice_keyboard().inline_keyboard for button in row]
    assert start_callbacks == ["onboarding:create", "onboarding:join"]
    admin_callbacks = [button.callback_data for row in settings_keyboard(False, True).inline_keyboard for button in row]
    assert "settings:reset" in admin_callbacks
    family_callbacks = [
        button.callback_data for row in family_keyboard(True).inline_keyboard for button in row
    ]
    assert family_callbacks == ["family:invite"]
    assert family_keyboard(False) is None


def test_render_postgres_url_normalization():
    assert normalize_database_url("postgres://user:pass@host/db") == "postgresql+asyncpg://user:pass@host/db"
    assert normalize_database_url("postgresql://user:pass@host/db") == "postgresql+asyncpg://user:pass@host/db"
    sqlite_url = "sqlite+aiosqlite:///sleep_tracker.db"
    assert normalize_database_url(sqlite_url) == (
        "sqlite+aiosqlite:///" + (PROJECT_DIR / "sleep_tracker.db").resolve().as_posix()
    )
    assert normalize_database_url("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"


def test_postgres_active_sleep_unique_index():
    index = next(item for item in SleepLog.__table__.indexes if item.name == "uq_sleep_one_active_per_child")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE INDEX" in ddl
    assert "WHERE end_time IS NULL" in ddl


def test_table_initialization_uses_if_not_exists():
    ddl = str(CreateTable(SleepLog.__table__, if_not_exists=True).compile(dialect=sqlite.dialect()))
    assert "CREATE TABLE IF NOT EXISTS" in ddl
