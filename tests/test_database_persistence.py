import asyncio
from datetime import date, datetime

from database import crud
from database.session import close_db, db_session, init_db, read_sqlite_bytes
from sqlalchemy import text


def test_repeated_initialization_preserves_existing_history(tmp_path):
    database_path = (tmp_path / "persistent.db").as_posix()
    database_url = f"sqlite+aiosqlite:///{database_path}"

    async def scenario() -> None:
        await init_db(database_url)
        async with db_session() as session:
            admin = await crud.create_family(
                session, 555001, "Тест сохранности", date(2025, 1, 1), "UTC"
            )
            await crud.start_sleep(
                session, admin.child_id, admin.id, datetime(2025, 3, 20, 10, 0)
            )
            await crud.save_ai_routine_snapshot(
                session,
                admin.child_id,
                '{"schedule":[{"time":"20:00","event":"Ночной сон"}]}',
                datetime(2025, 3, 20, 12, 0),
            )
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS legacy_records "
                "(id INTEGER PRIMARY KEY, child_id INTEGER, payload TEXT)"
            ))
            await session.execute(
                text("INSERT INTO legacy_records (child_id, payload) VALUES (:child_id, :payload)"),
                {"child_id": admin.child_id, "payload": "старые данные"},
            )
        await close_db()

        # Имитируем перезапуск приложения с повторным init_db на том же файле.
        await init_db(database_url)
        async with db_session() as session:
            admin = await crud.get_user(session, 555001)
            assert admin is not None
            active = await crud.active_sleep(session, admin.child_id)
            assert active is not None
            assert active.start_time == datetime(2025, 3, 20, 10, 0)
            snapshot = await crud.get_ai_routine_snapshot(session, admin.child_id)
            assert snapshot is not None
            assert "20:00" in snapshot.payload_json
            legacy_payload = await session.scalar(text("SELECT payload FROM legacy_records LIMIT 1"))
            assert legacy_payload == "старые данные"
        backup_payload = await read_sqlite_bytes(database_path)
        assert backup_payload.startswith(b"SQLite format 3")
        await close_db()

    asyncio.run(scenario())
