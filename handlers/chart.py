from __future__ import annotations

import asyncio
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from chart_generator import generate_sleep_chart
from database import crud
from database.session import db_session
from keyboards.inline import chart_period_keyboard
from services.norms import norm_for_age
from services.time_utils import age_parts, local_day_start_utc, to_local, utc_now


router = Router(name="chart")


async def _send_chart(message: Message, days: int) -> None:
    days = 14 if days >= 14 else 7
    now = utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        local_today = to_local(now, user.child.timezone).date()
        start_date = local_today - timedelta(days=days - 1)
        since = local_day_start_utc(user.child.timezone, now - timedelta(days=days - 1))
        sleeps = await crud.sleeps_overlapping(session, user.child_id, since, now)
        months, _ = age_parts(user.child.birth_date, local_today)
        norm = norm_for_age(months)
        timezone_name, child_name = user.child.timezone, user.child.name

    try:
        image = await asyncio.to_thread(
            generate_sleep_chart,
            sleeps,
            timezone_name,
            start_date,
            local_today,
            (norm.sleep_min + norm.sleep_max) / 2,
            child_name,
        )
    except Exception as exc:
        await message.answer(f"⚠️ Не удалось построить график: {exc}")
        return
    await message.answer_photo(
        BufferedInputFile(image.getvalue(), filename=f"sleep_chart_{days}d.png"),
        caption=f"📊 Сон за последние {days} дней • {child_name}",
        reply_markup=chart_period_keyboard(),
    )


@router.message(Command("chart"))
async def chart_command(message: Message, command: CommandObject) -> None:
    raw_days = (command.args or "7").strip()
    await _send_chart(message, 14 if raw_days == "14" else 7)


@router.message(F.text == "📊 График снов (Неделя)")
@router.message(F.text == "📊 График снов")
async def chart_button(message: Message) -> None:
    await _send_chart(message, 7)


@router.callback_query(F.data.regexp(r"^chart:(7|14)$"))
async def chart_period(callback: CallbackQuery) -> None:
    await callback.answer()
    days = int(callback.data.rsplit(":", 1)[1])
    await _send_chart(callback.message, days)

