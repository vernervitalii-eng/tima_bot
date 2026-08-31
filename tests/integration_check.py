"""Минимальный интеграционный smoke-test без обращения к Telegram API."""

import asyncio
from datetime import date, timedelta

from database import crud
from database.models import Child
from database.session import close_db, db_session, init_db
from handlers import register_handlers
from services.time_utils import utc_now


async def main() -> None:
    # In-memory SQLite keeps the smoke-test repeatable and does not leave a
    # disposable database in the project directory.
    await init_db("sqlite+aiosqlite:///:memory:")
    async with db_session() as session:
        admin = await crud.create_family(session, 1001, "Тест", date(2025, 1, 1), "Europe/Chisinau")
        code = admin.child.invite_code
    async with db_session() as session:
        member = await crud.join_family(session, 1002, code, "Мама")
        assert member and member.display_name == "Мама"
    now = utc_now()
    async with db_session() as session:
        admin = await crud.get_user(session, 1001)
        first = await crud.try_start_sleep(session, admin.child_id, admin.id, now)
        assert first is not None
        sleep_id = first.id
    async with db_session() as session:
        member = await crud.get_user(session, 1002)
        duplicate = await crud.try_start_sleep(session, member.child_id, member.id, now + timedelta(minutes=1))
        assert duplicate is None
    async with db_session() as session:
        member = await crud.get_user(session, 1002)
        active = await crud.active_sleep(session, member.child_id)
        assert active and active.id == sleep_id
        assert await crud.try_finish_sleep(session, active, now + timedelta(hours=1), member.child.timezone, member.id)
        assert not await crud.try_finish_sleep(session, active, now + timedelta(hours=1, minutes=1), member.child.timezone, member.id)
        await crud.add_activity(session, member.child_id, "feeding", now, "грудь", member.id)
    async with db_session() as session:
        member = await crud.get_user(session, 1002)
        feeding = await crud.latest_activity(session, member.child_id, "feeding")
        assert feeding and feeding.created_by_user_id == member.id

    # Администратор может заранее добавить участника по Telegram ID.
    async with db_session() as session:
        admin = await crud.get_user(session, 1001)
        invited, status = await crud.invite_family_member(session, admin.child_id, 1004)
        assert status == "created"
        assert invited.role == "member"
    async with db_session() as session:
        admin = await crud.get_user(session, 1001)
        invited, status = await crud.invite_family_member(session, admin.child_id, 1004)
        assert status == "already_member"
        assert invited.display_name == "Приглашённый участник"

    # Участник может выйти, не удаляя семейный профиль.
    async with db_session() as session:
        guest = await crud.join_family(session, 1003, code, "Няня")
        assert guest is not None
    async with db_session() as session:
        guest = await crud.get_user(session, 1003)
        await crud.leave_family(session, guest)
    async with db_session() as session:
        assert await crud.get_user(session, 1003) is None
        admin = await crud.get_user(session, 1001)
        assert admin is not None

    # Полный сброс администратора удаляет семью, участников и журналы.
    async with db_session() as session:
        admin = await crud.get_user(session, 1001)
        child_id = admin.child_id
        await crud.delete_family(session, child_id)
    async with db_session() as session:
        assert await crud.get_user(session, 1001) is None
        assert await crud.get_user(session, 1002) is None
        assert await crud.get_user(session, 1004) is None
        assert await session.get(Child, child_id) is None

    # Проверяем совместимость регистраций роутеров с установленной aiogram 3.x.
    from aiogram import Dispatcher
    dispatcher = Dispatcher()
    register_handlers(dispatcher)
    assert dispatcher.resolve_used_update_types()
    await close_db()
    print("integration check: passed")


if __name__ == "__main__":
    asyncio.run(main())
