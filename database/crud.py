from __future__ import annotations

import secrets
from datetime import date, datetime
from zoneinfo import ZoneInfo
from datetime import timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import ActivityLog, Child, SleepLog, SleepType, User, UserRole


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


async def family_telegram_ids(session: AsyncSession, child_id: int) -> list[int]:
    return list(await session.scalars(select(User.telegram_id).where(User.child_id == child_id)))


async def add_activity(
    session: AsyncSession, child_id: int, activity_type: str, at: datetime,
    details: str | None = None, created_by_user_id: int | None = None,
) -> ActivityLog:
    row = ActivityLog(
        child_id=child_id, activity_type=activity_type, timestamp=at,
        details=details, created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def family_members(session: AsyncSession, child_id: int) -> list[User]:
    return list(await session.scalars(select(User).where(User.child_id == child_id).order_by(User.id)))


async def update_birth_date(session: AsyncSession, child: Child, value: date) -> None:
    child.birth_date = value
    await session.flush()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def latest_activity(session: AsyncSession, child_id: int, activity_type: str) -> ActivityLog | None:
    return await session.scalar(
        select(ActivityLog)
        .where(ActivityLog.child_id == child_id, ActivityLog.activity_type == activity_type)
        .order_by(ActivityLog.timestamp.desc())
    )


async def activities_since(session: AsyncSession, child_id: int, since: datetime) -> list[ActivityLog]:
    return list(await session.scalars(
        select(ActivityLog)
        .where(ActivityLog.child_id == child_id, ActivityLog.timestamp >= since)
        .order_by(ActivityLog.timestamp)
    ))


async def leave_family(session: AsyncSession, user: User) -> None:
    """Удаляет только аккаунт участника, не затрагивая семейные данные."""
    await session.delete(user)
    await session.flush()


async def delete_family(session: AsyncSession, child_id: int) -> None:
    """Полностью удаляет профиль ребёнка и все связанные данные."""
    await session.execute(delete(ActivityLog).where(ActivityLog.child_id == child_id))
    await session.execute(delete(SleepLog).where(SleepLog.child_id == child_id))
    await session.execute(delete(User).where(User.child_id == child_id))
    await session.execute(delete(Child).where(Child.id == child_id))
    await session.flush()
