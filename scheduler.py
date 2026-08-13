from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from database import crud
from database.models import Child, SleepLog
from database.session import db_session
from services.norms import norm_for_age
from services.time_utils import age_parts, format_duration, is_quiet_hours, to_local, utc_now

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")


async def notify_family(bot: Bot, child_id: int, text: str) -> None:
    async with db_session() as session:
        recipients = await crud.family_telegram_ids(session, child_id)
        child = await session.get(Child, child_id)
        silent = bool(child and child.silent_mode and is_quiet_hours(child.timezone))
    for telegram_id in recipients:
        try:
            await bot.send_message(telegram_id, text, disable_notification=silent)
        except Exception:
            logger.warning("Не удалось отправить уведомление пользователю %s", telegram_id, exc_info=True)


async def wake_window_alert(bot: Bot, child_id: int, wake_at) -> None:
    async with db_session() as session:
        child = await session.get(Child, child_id)
        if not child or await crud.active_sleep(session, child_id):
            return
        if child.silent_mode and is_quiet_hours(child.timezone):
            return
        elapsed = utc_now() - wake_at
        text = (
            f"⏱ {child.name} бодрствует уже {format_duration(elapsed)}. "
            "Через 15 минут закрывается окно сна. Пора начинать ритуал укладывания!"
        )
    await notify_family(bot, child_id, text)


async def wake_overdue_alert(bot: Bot, child_id: int, wake_at) -> None:
    async with db_session() as session:
        child = await session.get(Child, child_id)
        if not child or await crud.active_sleep(session, child_id):
            return
        if child.silent_mode and is_quiet_hours(child.timezone):
            return
        text = f"🌤 {child.name} бодрствует заметно дольше возрастной нормы. Вы не забыли отметить укладывание?"
    await notify_family(bot, child_id, text)


async def sleep_overdue_alert(bot: Bot, child_id: int, sleep_id: int) -> None:
    async with db_session() as session:
        log = await session.get(SleepLog, sleep_id)
        child = await session.get(Child, child_id)
        if not log or log.end_time is not None or not child:
            return
        if child.silent_mode and is_quiet_hours(child.timezone):
            return
        # Ночной сон не считается забытым дневным таймером.
        local_hour = to_local(log.start_time, child.timezone).hour
        if local_hour >= 19 or local_hour < 6:
            return
        text = f"💤 {child.name} спит уже больше 4 часов. Вы не забыли отметить пробуждение?"
    await notify_family(bot, child_id, text)


def cancel_child_jobs(child_id: int) -> None:
    for suffix in ("window", "wake-overdue", "sleep-overdue"):
        job = scheduler.get_job(f"{suffix}:{child_id}")
        if job:
            job.remove()


def schedule_after_wake(bot: Bot, child_id: int, birth_date, wake_at) -> None:
    cancel_child_jobs(child_id)
    months, _ = age_parts(birth_date)
    norm = norm_for_age(months)
    window_at = wake_at + timedelta(minutes=max(norm.wake_max - 15, 1))
    overdue_at = wake_at + timedelta(minutes=norm.wake_max + 45)
    now = utc_now()
    if window_at > now:
        scheduler.add_job(wake_window_alert, "date", run_date=window_at, args=(bot, child_id, wake_at), id=f"window:{child_id}", replace_existing=True)
    if overdue_at > now:
        scheduler.add_job(wake_overdue_alert, "date", run_date=overdue_at, args=(bot, child_id, wake_at), id=f"wake-overdue:{child_id}", replace_existing=True)


def schedule_after_sleep(bot: Bot, child_id: int, sleep_id: int, start_at) -> None:
    cancel_child_jobs(child_id)
    run_at = start_at + timedelta(hours=4)
    if run_at > utc_now():
        scheduler.add_job(sleep_overdue_alert, "date", run_date=run_at, args=(bot, child_id, sleep_id), id=f"sleep-overdue:{child_id}", replace_existing=True)


async def restore_jobs(bot: Bot) -> None:
    async with db_session() as session:
        children = list(await session.scalars(select(Child)))
        for child in children:
            active = await crud.active_sleep(session, child.id)
            if active:
                schedule_after_sleep(bot, child.id, active.id, active.start_time)
            else:
                last = await crud.last_completed_sleep(session, child.id)
                if last and last.end_time:
                    schedule_after_wake(bot, child.id, child.birth_date, last.end_time)
