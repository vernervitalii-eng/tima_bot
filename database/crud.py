from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo
from datetime import timezone

from sqlalchemy import delete, func, inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Child, SleepLog, SleepType, User, UserRole


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id).options(selectinload(User.child))
    )
    return result.scalar_one_or_none()


async def create_family(
    session: AsyncSession, telegram_id: int, name: str, birth_date: date, timezone: str
) -> User:
    # token_urlsafe даёт криптографически случайный, удобный для Telegram код.
    while True:
        code = secrets.token_urlsafe(6).replace("-", "A").replace("_", "B").upper()[:8]
        if not await session.scalar(select(Child.id).where(Child.invite_code == code)):
            break
    child = Child(name=name, birth_date=birth_date, invite_code=code, timezone=timezone)
    user = User(telegram_id=telegram_id, role=UserRole.ADMIN.value, display_name="Администратор", child=child)
    session.add(user)
    await session.flush()
    return user


async def join_family(session: AsyncSession, telegram_id: int, code: str, display_name: str) -> User | None:
    child = await session.scalar(select(Child).where(Child.invite_code == code.upper()))
    if child is None:
        return None
    existing = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if existing:
        return existing
    user = User(
        telegram_id=telegram_id,
        child_id=child.id,
        role=UserRole.MEMBER.value,
        display_name=display_name[:80],
    )
    session.add(user)
    await session.flush()
    user.child = child
    return user


async def invite_family_member(
    session: AsyncSession, child_id: int, telegram_id: int
) -> tuple[User, str]:
    """Добавляет участника по Telegram ID и возвращает его вместе со статусом."""
    existing = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if existing:
        status = "already_member" if existing.child_id == child_id else "other_family"
        return existing, status

    user = User(
        telegram_id=telegram_id,
        child_id=child_id,
        role=UserRole.MEMBER.value,
        display_name="Приглашённый участник",
    )
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
        return user, "created"
    except IntegrityError:
        # Другой запрос мог одновременно пригласить тот же Telegram ID.
        existing = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if existing is None:
            raise
        status = "already_member" if existing.child_id == child_id else "other_family"
        return existing, status


async def active_sleep(session: AsyncSession, child_id: int) -> SleepLog | None:
    return await session.scalar(
        select(SleepLog)
        .where(SleepLog.child_id == child_id, SleepLog.end_time.is_(None))
        .order_by(SleepLog.start_time.desc())
    )


async def start_sleep(session: AsyncSession, child_id: int, user_id: int, at: datetime) -> SleepLog:
    log = SleepLog(child_id=child_id, created_by_user_id=user_id, start_time=at, sleep_type=SleepType.DAY.value)
    session.add(log)
    await session.flush()
    return log


async def try_start_sleep(session: AsyncSession, child_id: int, user_id: int, at: datetime) -> SleepLog | None:
    """Атомарный старт: partial unique index гасит одновременные клики."""
    log = SleepLog(child_id=child_id, created_by_user_id=user_id, start_time=at, sleep_type=SleepType.DAY.value)
    try:
        async with session.begin_nested():
            session.add(log)
            await session.flush()
        return log
    except IntegrityError:
        return None


async def finish_sleep(session: AsyncSession, log: SleepLog, at: datetime, timezone_name: str = "UTC") -> SleepLog:
    log.end_time = at
    # Ночным считаем сон, начатый вечером/ночью, либо длинный сон, пересекающий ночь.
    duration_hours = (at - log.start_time).total_seconds() / 3600
    local_start = log.start_time.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name))
    if local_start.hour >= 19 or local_start.hour < 6 or duration_hours >= 5:
        log.sleep_type = SleepType.NIGHT.value
    await session.flush()
    return log


async def try_finish_sleep(
    session: AsyncSession, log: SleepLog, at: datetime, timezone_name: str, ended_by_user_id: int
) -> bool:
    duration_hours = (at - log.start_time).total_seconds() / 3600
    local_start = log.start_time.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name))
    sleep_type = (
        SleepType.NIGHT.value
        if local_start.hour >= 19 or local_start.hour < 6 or duration_hours >= 5
        else SleepType.DAY.value
    )
    result = await session.execute(
        update(SleepLog)
        .where(SleepLog.id == log.id, SleepLog.end_time.is_(None))
        .values(end_time=at, sleep_type=sleep_type, ended_by_user_id=ended_by_user_id)
    )
    return result.rowcount == 1


async def previous_completed_sleep(session: AsyncSession, child_id: int, before: datetime) -> SleepLog | None:
    return await session.scalar(
        select(SleepLog)
        .where(SleepLog.child_id == child_id, SleepLog.end_time.is_not(None), SleepLog.end_time <= before)
        .order_by(SleepLog.end_time.desc())
    )


async def last_completed_sleep(session: AsyncSession, child_id: int) -> SleepLog | None:
    return await session.scalar(
        select(SleepLog)
        .where(SleepLog.child_id == child_id, SleepLog.end_time.is_not(None))
        .order_by(SleepLog.end_time.desc())
    )


async def sleeps_overlapping(session: AsyncSession, child_id: int, since: datetime, until: datetime) -> list[SleepLog]:
    rows = await session.scalars(
        select(SleepLog).where(
            SleepLog.child_id == child_id,
            SleepLog.start_time < until,
            or_(SleepLog.end_time.is_(None), SleepLog.end_time > since),
        ).order_by(SleepLog.start_time)
    )
    return list(rows)


async def completed_sleeps_since(
    session: AsyncSession, child_id: int, since: datetime
) -> list[SleepLog]:
    rows = await session.scalars(
        select(SleepLog)
        .where(
            SleepLog.child_id == child_id,
            SleepLog.end_time.is_not(None),
            SleepLog.end_time >= since,
        )
        .order_by(SleepLog.start_time)
    )
    return list(rows)


async def family_telegram_ids(session: AsyncSession, child_id: int) -> list[int]:
    return list(await session.scalars(select(User.telegram_id).where(User.child_id == child_id)))


async def family_members(session: AsyncSession, child_id: int) -> list[User]:
    return list(await session.scalars(select(User).where(User.child_id == child_id).order_by(User.id)))


async def update_birth_date(session: AsyncSession, child: Child, value: date) -> None:
    child.birth_date = value
    await session.flush()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def seed_monthly_data(
    session: AsyncSession,
    child_id: int,
    user_id: int,
    sleeps: Iterable[object],
) -> dict[str, int]:
    """Идемпотентно добавляет распарсенную историю, не изменяя существующие строки.

    Объекты принимаются по атрибутам ``start``, ``end`` и ``sleep_type``.
    Это сознательно отделяет NLP-парсер от ORM.
    Дубликатом считается тот же ребёнок и та же дата/время события.
    """
    result = {"sleep_added": 0, "sleep_skipped": 0}
    for item in sleeps:
        start = getattr(item, "start")
        end = getattr(item, "end", None)
        exists = await session.scalar(
            select(SleepLog.id).where(
                SleepLog.child_id == child_id,
                SleepLog.start_time == start,
                SleepLog.end_time == end,
            ).limit(1)
        )
        if exists is not None:
            result["sleep_skipped"] += 1
            continue
        if end is None:
            # Частичный уникальный индекс разрешает только один активный сон.
            active = await active_sleep(session, child_id)
            if active is not None:
                result["sleep_skipped"] += 1
                continue
        session.add(SleepLog(
            child_id=child_id,
            start_time=start,
            end_time=end,
            sleep_type=getattr(item, "sleep_type", SleepType.DAY.value),
            created_by_user_id=user_id,
            ended_by_user_id=user_id if end is not None else None,
        ))
        result["sleep_added"] += 1

    await session.flush()
    return result


async def sleep_history_page(
    session: AsyncSession,
    child_id: int,
    page: int = 0,
    page_size: int = 10,
) -> tuple[list[SleepLog], int]:
    """Возвращает одну страницу записей сна и общее число записей."""
    page = max(page, 0)
    page_size = min(max(page_size, 1), 50)
    total = int(await session.scalar(
        select(func.count(SleepLog.id)).where(SleepLog.child_id == child_id)
    ) or 0)
    rows = await session.scalars(
        select(SleepLog)
        .where(SleepLog.child_id == child_id)
        .order_by(SleepLog.start_time.desc(), SleepLog.id.desc())
        .offset(page * page_size)
        .limit(page_size)
    )
    return list(rows), total


async def sleep_by_id(session: AsyncSession, child_id: int, log_id: int) -> SleepLog | None:
    return await session.scalar(
        select(SleepLog).where(SleepLog.id == log_id, SleepLog.child_id == child_id)
    )


async def delete_sleep_log(session: AsyncSession, child_id: int, log_id: int) -> bool:
    """Удаляет только выбранную запись сна текущего семейного профиля."""
    result = await session.execute(
        delete(SleepLog).where(SleepLog.id == log_id, SleepLog.child_id == child_id)
    )
    await session.flush()
    return result.rowcount == 1


async def leave_family(session: AsyncSession, user: User) -> None:
    """Удаляет только аккаунт участника, не затрагивая семейные данные."""
    await session.delete(user)
    await session.flush()


async def delete_family(session: AsyncSession, child_id: int) -> None:
    """Полностью удаляет профиль ребёнка и все связанные данные."""
    # Ранние версии создавали activity_logs. Рабочий код больше не использует
    # эту таблицу, но при явно подтверждённом удалении семьи legacy-строки надо
    # убрать до пользователей, иначе старый FK может заблокировать операцию.
    connection = await session.connection()
    has_legacy_activity_table = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).has_table("activity_logs")
    )
    if has_legacy_activity_table:
        await session.execute(text("DELETE FROM activity_logs WHERE child_id = :child_id"), {"child_id": child_id})
    await session.execute(delete(SleepLog).where(SleepLog.child_id == child_id))
    await session.execute(delete(User).where(User.child_id == child_id))
    await session.execute(delete(Child).where(Child.id == child_id))
    await session.flush()
