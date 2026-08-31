from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import crud
from database.session import db_session
from keyboards.inline import day_date_keyboard, day_period_keyboard
from services.day_timeline import build_day_timeline, local_date_bounds_utc
from services.norms import norm_for_age
from services.time_utils import age_parts, is_quiet_hours, to_local, utc_now


router = Router(name="day")


async def _load_day(telegram_id: int, selected_date: date) -> tuple[str, bool] | None:
    now = utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, telegram_id)
        if not user:
            return None
        today = to_local(now, user.child.timezone).date()
        if selected_date > today:
            selected_date = today
        since, until = local_date_bounds_utc(selected_date, user.child.timezone)
        query_until = min(until, now) if selected_date == today else until
        logs = await crud.sleeps_overlapping(session, user.child_id, since, query_until)
        activities = await crud.activities_between(session, user.child_id, since, query_until)
        months, _ = age_parts(user.child.birth_date, selected_date)
        norm = norm_for_age(months)
        wake_target = round((norm.wake_min + norm.wake_max) / 2)
        text = build_day_timeline(
            user.child.name,
            selected_date,
            user.child.timezone,
            logs,
            activities,
            wake_target,
            now,
        )
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)
    return text, silent


@router.message(Command("day"))
@router.message(F.text == "📅 Хронология дня")
async def day_view(message: Message) -> None:
    now = utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        today = to_local(now, user.child.timezone).date()
    loaded = await _load_day(message.from_user.id, today)
    if loaded is None:
        await message.answer("Сначала выполните /start.")
        return
    text, silent = loaded
    await message.answer(text, reply_markup=day_period_keyboard(), disable_notification=silent)


@router.callback_query(F.data.in_({"day:today", "day:yesterday", "day:pick"}))
async def day_period(callback: CallbackQuery) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала выполните /start", show_alert=True)
            return
        today = to_local(utc_now(), user.child.timezone).date()
    if callback.data == "day:pick":
        await callback.message.answer("🗓 <b>Выберите дату</b>", reply_markup=day_date_keyboard(today))
        await callback.answer()
        return
    selected = today if callback.data == "day:today" else today - timedelta(days=1)
    await _edit_day(callback, selected)


@router.callback_query(F.data.startswith("day:date:"))
async def day_selected(callback: CallbackQuery) -> None:
    try:
        selected = date.fromisoformat(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    await _edit_day(callback, selected)


async def _edit_day(callback: CallbackQuery, selected: date) -> None:
    loaded = await _load_day(callback.from_user.id, selected)
    if loaded is None:
        await callback.answer("Сначала выполните /start", show_alert=True)
        return
    text, _ = loaded
    try:
        await callback.message.edit_text(text, reply_markup=day_period_keyboard())
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer()
