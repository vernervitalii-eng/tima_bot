import asyncio
from datetime import date, datetime

from database import crud
from database.session import close_db, db_session, init_db


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
            await crud.add_activity(
                session,
                admin.child_id,
                "feeding",
                datetime(2025, 3, 20, 9, 30),
                "грудь",
                admin.id,
            )
        await close_db()

        # Имитируем перезапуск приложения с повторным init_db на том же файле.
        await init_db(database_url)
        async with db_session() as session:
            admin = await crud.get_user(session, 555001)
            assert admin is not None
            active = await crud.active_sleep(session, admin.child_id)
            feeding = await crud.latest_activity(session, admin.child_id, "feeding")
            assert active is not None
            assert active.start_time == datetime(2025, 3, 20, 10, 0)
            assert feeding is not None
            assert feeding.details == "грудь"
        await close_db()

    asyncio.run(scenario())
