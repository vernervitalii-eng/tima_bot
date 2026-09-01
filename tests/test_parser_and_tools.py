import asyncio
from datetime import date
from pathlib import Path

from database import crud
from database.session import close_db, db_session, init_db
from parser import parse_text


def test_parser_supports_only_sleep_ranges_and_dates():
    result = parse_text(
        "04.08 10:15-11:30 спал\n"
        "вчера уснул в 21:00 проснулся в 07:30",
        date(2026, 8, 31),
    )
    assert any(item.duration_minutes == 75 for item in result.sleeps)
    assert any(item.start.date() == date(2026, 8, 30) and item.end.date() == date(2026, 8, 31) for item in result.sleeps if item.end)
    assert len(result.sleeps) == 2


def test_august_seed_source_is_parseable():
    source = Path("data/august_2026.txt").read_text(encoding="utf-8")
    result = parse_text(source, date(2026, 8, 31))
    assert len(result.sleeps) >= 130
    assert result.sleeps[0].start.date() == date(2026, 8, 1)
    assert result.sleeps[-1].start.date() == date(2026, 8, 31)
    assert result.warnings  # ambiguous source rows are reported, not silently hidden


def test_seed_monthly_data_is_idempotent(tmp_path):
    db_path = (tmp_path / "seed.db").as_posix()
    parsed = parse_text("01.08\n08:00 проснулся\n09:30 уснул\n10:15 проснулся", date(2026, 8, 31))

    async def scenario():
        await init_db(f"sqlite+aiosqlite:///{db_path}")
        async with db_session() as session:
            admin = await crud.create_family(session, 902001, "Seed", date(2025, 1, 1), "UTC")
            first = await crud.seed_monthly_data(session, admin.child_id, admin.id, parsed.sleeps)
            second = await crud.seed_monthly_data(session, admin.child_id, admin.id, parsed.sleeps)
            assert first["sleep_added"] == 1
            assert second["sleep_added"] == 0
            assert second["sleep_skipped"] == 1
            rows, total = await crud.sleep_history_page(session, admin.child_id)
            assert total == 1
            assert await crud.delete_sleep_log(session, admin.child_id, rows[0].id)
            _, total_after_delete = await crud.sleep_history_page(session, admin.child_id)
            assert total_after_delete == 0
        await close_db()

    asyncio.run(scenario())
