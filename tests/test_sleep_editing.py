import asyncio
from datetime import date, datetime

from database import crud
from database.models import SleepLog, SleepType
from database.session import close_db, db_session, init_db
from handlers.history import _record_text
from services.time_utils import parse_anchored_local_time


async def _create_family(telegram_id: int, name: str):
    async with db_session() as session:
        user = await crud.create_family(
            session,
            telegram_id,
            name,
            date(2025, 1, 1),
            "UTC",
        )
        return user.child_id, user.id


def test_create_sleep_interval_handles_duplicates_overlaps_and_touching_bounds(tmp_path):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{(tmp_path / 'create-interval.db').as_posix()}"
        await init_db(database_url)
        try:
            child_id, user_id = await _create_family(710001, "Добавление")

            async with db_session() as session:
                original, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2025, 3, 20, 10, 0),
                    datetime(2025, 3, 20, 11, 0),
                    "UTC",
                )
                assert status == "created"
                assert original is not None
                original_id = original.id

            async with db_session() as session:
                duplicate, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2025, 3, 20, 10, 0),
                    datetime(2025, 3, 20, 11, 0),
                    "UTC",
                )
                assert status == "duplicate"
                assert duplicate is not None and duplicate.id == original_id

                _, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2025, 3, 20, 10, 30),
                    datetime(2025, 3, 20, 11, 30),
                    "UTC",
                )
                assert status == "overlap"

                before, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2025, 3, 20, 9, 0),
                    datetime(2025, 3, 20, 10, 0),
                    "UTC",
                )
                assert status == "created" and before is not None

                after, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2025, 3, 20, 11, 0),
                    datetime(2025, 3, 20, 12, 0),
                    "UTC",
                )
                assert status == "created" and after is not None

            async with db_session() as session:
                rows, total = await crud.sleep_history_page(session, child_id, page_size=20)
                assert total == 3
                assert {row.id for row in rows} == {original_id, before.id, after.id}
        finally:
            await close_db()

    asyncio.run(scenario())


def test_update_sleep_interval_rejects_neighbor_overlap_without_partial_write(tmp_path):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{(tmp_path / 'update-interval.db').as_posix()}"
        await init_db(database_url)
        try:
            child_id, user_id = await _create_family(710002, "Редактирование")
            intervals = [
                (datetime(2025, 3, 20, 9, 0), datetime(2025, 3, 20, 10, 0)),
                (datetime(2025, 3, 20, 11, 0), datetime(2025, 3, 20, 12, 0)),
                (datetime(2025, 3, 20, 13, 0), datetime(2025, 3, 20, 14, 0)),
            ]
            ids = []
            async with db_session() as session:
                for start, end in intervals:
                    log, status = await crud.create_sleep_interval(
                        session, child_id, user_id, start, end, "UTC"
                    )
                    assert status == "created" and log is not None
                    ids.append(log.id)

            middle_id = ids[1]
            original_start, original_end = intervals[1]

            async with db_session() as session:
                _, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    middle_id,
                    intervals[0][0],
                    intervals[0][1],
                    "UTC",
                )
                assert status == "duplicate"

                _, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    middle_id,
                    original_start,
                    original_start,
                    "UTC",
                )
                assert status == "invalid"

                _, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    middle_id,
                    datetime(2025, 3, 20, 9, 59),
                    original_end,
                    "UTC",
                )
                assert status == "overlap"

            async with db_session() as session:
                middle = await crud.sleep_by_id(session, child_id, middle_id)
                assert middle is not None
                assert (middle.start_time, middle.end_time) == (original_start, original_end)

                _, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    middle_id,
                    original_start,
                    datetime(2025, 3, 20, 13, 1),
                    "UTC",
                )
                assert status == "overlap"

            async with db_session() as session:
                middle = await crud.sleep_by_id(session, child_id, middle_id)
                assert middle is not None
                assert (middle.start_time, middle.end_time) == (original_start, original_end)

                updated, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    middle_id,
                    datetime(2025, 3, 20, 10, 0),
                    datetime(2025, 3, 20, 13, 0),
                    "UTC",
                )
                assert status == "updated"
                assert updated is not None and updated.id == middle_id

            async with db_session() as session:
                middle = await crud.sleep_by_id(session, child_id, middle_id)
                _, total = await crud.sleep_history_page(session, child_id, page_size=20)
                assert middle is not None
                assert (middle.start_time, middle.end_time) == (
                    datetime(2025, 3, 20, 10, 0),
                    datetime(2025, 3, 20, 13, 0),
                )
                assert total == 3
        finally:
            await close_db()

    asyncio.run(scenario())


def test_completed_interval_respects_active_sleep_and_rejects_full_day(tmp_path):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{(tmp_path / 'active-overlap.db').as_posix()}"
        await init_db(database_url)
        try:
            child_id, user_id = await _create_family(710006, "Активный сон")
            async with db_session() as session:
                active = await crud.try_start_sleep(
                    session,
                    child_id,
                    user_id,
                    datetime(2026, 8, 20, 15, 0),
                )
                assert active is not None

            async with db_session() as session:
                _, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2026, 8, 20, 14, 30),
                    datetime(2026, 8, 20, 15, 30),
                    "UTC",
                )
                assert status == "overlap"

                adjacent, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2026, 8, 20, 14, 0),
                    datetime(2026, 8, 20, 15, 0),
                    "UTC",
                )
                assert status == "created" and adjacent is not None

                _, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    user_id,
                    datetime(2026, 8, 18, 10, 0),
                    datetime(2026, 8, 19, 10, 0),
                    "UTC",
                )
                assert status == "invalid"
        finally:
            await close_db()

    asyncio.run(scenario())


def test_sleep_interval_operations_are_isolated_by_child(tmp_path):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{(tmp_path / 'child-isolation.db').as_posix()}"
        await init_db(database_url)
        try:
            first_child_id, first_user_id = await _create_family(710003, "Первый")
            second_child_id, second_user_id = await _create_family(710004, "Второй")
            start = datetime(2025, 3, 20, 10, 0)
            end = datetime(2025, 3, 20, 11, 0)

            async with db_session() as session:
                second_log, status = await crud.create_sleep_interval(
                    session, second_child_id, second_user_id, start, end, "UTC"
                )
                assert status == "created" and second_log is not None
                second_log_id = second_log.id

            async with db_session() as session:
                inaccessible, status = await crud.update_sleep_interval(
                    session,
                    first_child_id,
                    second_log_id,
                    datetime(2025, 3, 20, 11, 0),
                    datetime(2025, 3, 20, 12, 0),
                    "UTC",
                )
                assert status == "not_found"
                assert inaccessible is None

                first_log, status = await crud.create_sleep_interval(
                    session, first_child_id, first_user_id, start, end, "UTC"
                )
                assert status == "created" and first_log is not None

            async with db_session() as session:
                untouched = await crud.sleep_by_id(session, second_child_id, second_log_id)
                _, first_total = await crud.sleep_history_page(session, first_child_id)
                _, second_total = await crud.sleep_history_page(session, second_child_id)
                assert untouched is not None
                assert (untouched.start_time, untouched.end_time) == (start, end)
                assert first_total == 1
                assert second_total == 1
        finally:
            await close_db()

    asyncio.run(scenario())


def test_history_time_is_anchored_to_original_date():
    anchor = date(2026, 8, 17)
    assert parse_anchored_local_time("14:15", anchor, "UTC") == datetime(
        2026, 8, 17, 14, 15
    )
    assert parse_anchored_local_time("18.08.2026 09:40", anchor, "UTC") == datetime(
        2026, 8, 18, 9, 40
    )
    assert parse_anchored_local_time("31.02.2026 09:40", anchor, "UTC") is None


def test_history_record_card_keeps_position_and_duration():
    log = SleepLog(
        child_id=1,
        created_by_user_id=1,
        start_time=datetime(2026, 8, 17, 10, 0),
        end_time=datetime(2026, 8, 17, 11, 15),
        sleep_type=SleepType.DAY.value,
    )
    card = _record_text(log, 12, "UTC")
    assert "12. <code>" in card
    assert "17.08 10:00 – 11:15" in card
    assert "1ч 15м" in card


def test_update_detects_stale_parent_edit_and_reclassifies_sleep_type(tmp_path):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{(tmp_path / 'stale-edit.db').as_posix()}"
        await init_db(database_url)
        try:
            child_id, user_id = await _create_family(710005, "Семейная правка")
            start = datetime(2026, 8, 20, 20, 0)
            end = datetime(2026, 8, 20, 21, 0)
            async with db_session() as session:
                log, status = await crud.create_sleep_interval(
                    session, child_id, user_id, start, end, "UTC"
                )
                assert status == "created"
                assert log is not None and log.sleep_type == SleepType.NIGHT.value
                log_id = log.id

            async with db_session() as session:
                _, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    log_id,
                    datetime(2026, 8, 20, 10, 0),
                    datetime(2026, 8, 20, 11, 0),
                    "UTC",
                    expected_start=datetime(2026, 8, 20, 19, 55),
                    expected_end=end,
                    check_expected=True,
                )
                assert status == "stale"

            async with db_session() as session:
                unchanged = await crud.sleep_by_id(session, child_id, log_id)
                assert unchanged is not None
                assert (unchanged.start_time, unchanged.end_time) == (start, end)
                updated, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    log_id,
                    datetime(2026, 8, 20, 10, 0),
                    datetime(2026, 8, 20, 11, 0),
                    "UTC",
                )
                assert status == "updated"
                assert updated is not None
                assert updated.sleep_type == SleepType.DAY.value
        finally:
            await close_db()

    asyncio.run(scenario())


def test_start_edit_preserves_wake_author_until_end_changes(tmp_path):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{(tmp_path / 'wake-author.db').as_posix()}"
        await init_db(database_url)
        try:
            child_id, first_user_id = await _create_family(710007, "Первый родитель")
            async with db_session() as session:
                second_user, invite_status = await crud.invite_family_member(
                    session,
                    child_id,
                    710008,
                )
                assert invite_status == "created"
                second_user_id = second_user.id
                log, status = await crud.create_sleep_interval(
                    session,
                    child_id,
                    first_user_id,
                    datetime(2026, 8, 20, 10, 0),
                    datetime(2026, 8, 20, 11, 0),
                    "UTC",
                )
                assert status == "created" and log is not None
                log_id = log.id

            async with db_session() as session:
                updated, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    log_id,
                    datetime(2026, 8, 20, 9, 55),
                    datetime(2026, 8, 20, 11, 0),
                    "UTC",
                    edited_by_user_id=second_user_id,
                )
                assert status == "updated" and updated is not None
                assert updated.ended_by_user_id == first_user_id

            async with db_session() as session:
                updated, status = await crud.update_sleep_interval(
                    session,
                    child_id,
                    log_id,
                    datetime(2026, 8, 20, 9, 55),
                    datetime(2026, 8, 20, 11, 5),
                    "UTC",
                    edited_by_user_id=second_user_id,
                )
                assert status == "updated" and updated is not None
                assert updated.ended_by_user_id == second_user_id
        finally:
            await close_db()

    asyncio.run(scenario())
