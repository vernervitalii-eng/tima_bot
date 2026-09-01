from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import Child
from database.session import db_session
from keyboards.main import main_keyboard
from services.norms import norm_for_age
from services.sleep_insights import typical_wake_minutes
from services.time_utils import age_parts, format_duration, is_quiet_hours, to_local, utc_now


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LiveStatusView:
    text: str
    is_sleeping: bool
    child_id: int
    silent: bool


def build_live_status_card(
    child_name: str,
    timezone_name: str,
    now: datetime,
    active_sleep,
    last_completed_sleep,
    typical_wake: int,
) -> str:
    """Формирует крупную HTML-карточку из единого снимка БД."""
    child_line = f"👶 <b>{escape(child_name)}</b>\n"
    if active_sleep is not None:
        local_start = to_local(active_sleep.start_time, timezone_name)
        elapsed = max(now - active_sleep.start_time, timedelta())
        return (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 <b>СТАТУС: РЕБЁНОК СПИТ 💤</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{child_line}"
            f"⏱ Уснул в: <code>{local_start:%H:%M}</code> "
            f"(спит уже: <code>{format_duration(elapsed)}</code>)\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    if last_completed_sleep is not None and last_completed_sleep.end_time is not None:
        wake_at = last_completed_sleep.end_time
        local_wake = to_local(wake_at, timezone_name)
        target = to_local(wake_at + timedelta(minutes=typical_wake), timezone_name)
        elapsed = max(now - wake_at, timedelta())
        return (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🟡 <b>СТАТУС: РЕБЁНОК БОДРСТВУЕТ ☀️</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{child_line}"
            f"⏱ Проснулся в: <code>{local_wake:%H:%M}</code> "
            f"(бодрствует: <code>{format_duration(elapsed)}</code>)\n"
            f"🎯 Окно в следующий сон: <code>~{target:%H:%M}</code> "
            "<i>(по среднему ВБ)</i>\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🟡 <b>СТАТУС: РЕБЁНОК БОДРСТВУЕТ ☀️</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{child_line}"
        "⏱ Время последнего пробуждения ещё не записано.\n"
        "🎯 Окно в следующий сон появится после первой завершённой записи.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


async def build_live_status_view(
    session: AsyncSession,
    child: Child,
    now: datetime | None = None,
) -> LiveStatusView:
    current = now or utc_now()
    active, last_completed, history = await crud.sleep_status_snapshot(
        session,
        child.id,
        current - timedelta(days=14),
    )
    local_today = to_local(current, child.timezone).date()
    age_months, _ = age_parts(child.birth_date, local_today)
    norm = norm_for_age(age_months)
    fallback_wake = round((norm.wake_min + norm.wake_max) / 2)
    typical_wake, _ = typical_wake_minutes(history, fallback_wake)
    return LiveStatusView(
        text=build_live_status_card(
            child.name,
            child.timezone,
            current,
            active,
            last_completed,
            typical_wake,
        ),
        is_sleeping=active is not None,
        child_id=child.id,
        silent=child.silent_mode and is_quiet_hours(child.timezone, current),
    )


async def broadcast_live_status(bot: Bot, child_id: int) -> None:
    """Синхронизирует карточку и Reply-клавиатуру у всей семьи."""
    async with db_session() as session:
        child = await session.get(Child, child_id)
        if child is None:
            return
        recipients = await crud.family_telegram_ids(session, child_id)
        view = await build_live_status_view(session, child)

    for telegram_id in recipients:
        try:
            await bot.send_message(
                telegram_id,
                view.text,
                reply_markup=main_keyboard(view.is_sleeping),
                disable_notification=view.silent,
            )
        except Exception:
            logger.warning(
                "Не удалось синхронизировать статус пользователю %s",
                telegram_id,
                exc_info=True,
            )


async def resync_child_runtime(bot: Bot, child_id: int) -> None:
    """Перестраивает напоминания и рассылает семье статус после ручной правки."""
    from services.scheduler import cancel_child_jobs, schedule_after_sleep, schedule_after_wake

    async with db_session() as session:
        child = await session.get(Child, child_id)
        if child is None:
            return
        active = await crud.active_sleep(session, child_id)
        last = await crud.last_completed_sleep(session, child_id)
        birth_date = child.birth_date

    cancel_child_jobs(child_id)
    if active is not None:
        schedule_after_sleep(bot, child_id, active.id, active.start_time)
    elif last is not None and last.end_time is not None:
        schedule_after_wake(bot, child_id, birth_date, last.end_time)
    await broadcast_live_status(bot, child_id)
