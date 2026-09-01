from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from database.models import Child
from database.session import db_session
from handlers.states import EditTime
from keyboards.inline import edit_time_keyboard
from keyboards.main import main_keyboard
from services.scheduler import schedule_after_sleep, schedule_after_wake
from services.live_status import broadcast_live_status, resync_child_runtime
from services.norms import norm_for_age
from services.sleep_insights import build_wake_widget, typical_wake_minutes
from services.time_utils import (
    age_parts,
    format_duration,
    is_quiet_hours,
    parse_anchored_local_time,
    parse_relative_time,
    to_local,
    utc_now,
)

router = Router(name="sleep")


async def do_sleep_start(message: Message, at=None) -> None:
    at = at or utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        existing = await crud.active_sleep(session, user.child_id)
        if existing:
            creator = await crud.get_user_by_id(session, existing.created_by_user_id)
            local_start = to_local(existing.start_time, user.child.timezone)
            author = creator.display_name if creator else "член семьи"
            await message.answer(
                f"{user.child.name} уже спит с {local_start:%H:%M} (отметил(а): {author}).",
                reply_markup=main_keyboard(True),
                disable_notification=user.child.silent_mode and is_quiet_hours(user.child.timezone),
            )
            return
        previous = await crud.last_completed_sleep(session, user.child_id)
        if previous and previous.end_time and at <= previous.end_time:
            await message.answer("Время укладывания должно быть позже прошлого пробуждения.")
            return
        log = await crud.try_start_sleep(session, user.child_id, user.id, at)
        if log is None:
            existing = await crud.active_sleep(session, user.child_id)
            creator = await crud.get_user_by_id(session, existing.created_by_user_id) if existing else None
            local_start = to_local(existing.start_time, user.child.timezone) if existing else None
            await message.answer(
                f"{user.child.name} уже спит"
                + (f" с {local_start:%H:%M} (отметил(а): {creator.display_name if creator else 'член семьи'})." if local_start else "."),
                reply_markup=main_keyboard(True),
            )
            return
        child_id, name, timezone, log_id = user.child_id, user.child.name, user.child.timezone, log.id
        author = user.display_name
    schedule_after_sleep(message.bot, child_id, log_id, at)
    local = to_local(at, timezone)
    text = f"💤 {name} уснул(а) в {local:%H:%M}. Приятного отдыха!\n\n<i>Запись добавил(а): {author}</i>"
    async with db_session() as session:
        ids = await crud.family_telegram_ids(session, child_id)
        child = await session.get(Child, child_id)
        silent = child.silent_mode and is_quiet_hours(child.timezone)
    for telegram_id in ids:
        try:
            await message.bot.send_message(
                telegram_id, text, reply_markup=edit_time_keyboard(log_id, "start"),
                disable_notification=silent,
            )
        except Exception:
            pass
    await broadcast_live_status(message.bot, child_id)


async def do_wake(message: Message, at=None) -> None:
    at = at or utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        log = await crud.active_sleep(session, user.child_id)
        if not log:
            last = await crud.last_completed_sleep(session, user.child_id)
            author = await crud.get_user_by_id(session, last.ended_by_user_id) if last and last.ended_by_user_id else None
            detail = (
                f" с {to_local(last.end_time, user.child.timezone):%H:%M}"
                f" (отметил(а): {author.display_name if author else 'член семьи'})"
                if last and last.end_time else ""
            )
            await message.answer(
                f"{user.child.name} уже бодрствует{detail}.",
                reply_markup=main_keyboard(False),
                disable_notification=user.child.silent_mode and is_quiet_hours(user.child.timezone),
            )
            return
        if at <= log.start_time or at > utc_now() + timedelta(minutes=2):
            await message.answer("Время пробуждения должно быть позже времени засыпания.")
            return
        previous = await crud.previous_completed_sleep(session, user.child_id, log.start_time)
        wake_before = log.start_time - previous.end_time if previous and previous.end_time else None
        if not await crud.try_finish_sleep(session, log, at, user.child.timezone, user.id):
            await message.answer("Пробуждение уже отметил другой член семьи.", reply_markup=main_keyboard(False))
            return
        duration = at - log.start_time
        history = await crud.completed_sleeps_since(session, user.child_id, at - timedelta(days=14))
        ids = await crud.family_telegram_ids(session, user.child_id)
        name, timezone, child_id, birth = user.child.name, user.child.timezone, user.child_id, user.child.birth_date
        local = to_local(at, timezone)
        months, _ = age_parts(birth, local.date())
        norm = norm_for_age(months)
        fallback_wake = round((norm.wake_min + norm.wake_max) / 2)
        typical_wake, history_samples = typical_wake_minutes(history, fallback_wake)
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)
        text = build_wake_widget(
            name,
            at,
            duration,
            typical_wake,
            timezone,
            history_samples,
            user.display_name,
            wake_before,
        )
    schedule_after_wake(message.bot, child_id, birth, at)
    for telegram_id in ids:
        try:
            await message.bot.send_message(
                telegram_id, text,
                reply_markup=edit_time_keyboard(log.id, "end"),
                disable_notification=silent,
            )
        except Exception:
            pass
    await broadcast_live_status(message.bot, child_id)


@router.message(F.text.in_({"💤 Уснул", "💤 Уснул сейчас"}))
async def sleep_now(message: Message) -> None:
    await do_sleep_start(message)


@router.message(F.text == "☀️ Проснулся")
async def wake_now(message: Message) -> None:
    await do_wake(message)


@router.callback_query(F.data.startswith("edit:"))
async def edit_time_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, field, raw_id = callback.data.split(":")
    if field not in {"start", "end"}:
        await callback.message.answer("Кнопка устарела. Откройте запись заново.")
        return
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        log = await crud.sleep_by_id(session, user.child_id, int(raw_id)) if user else None
        if not user or not log or log.child_id != user.child_id:
            await callback.message.answer("Запись недоступна.")
            return
    await state.set_state(EditTime.value)
    await state.update_data(
        log_id=int(raw_id),
        field=field,
        expected_start=log.start_time,
        expected_end=log.end_time,
    )
    await callback.message.answer("Введите фактическое время как ЧЧ:ММ или, например, «20 минут назад». /cancel — отмена.")


@router.callback_query(F.data.startswith("adjust:"))
async def adjust_time(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    _, field, raw_id, raw_delta = callback.data.split(":")
    if field not in {"start", "end"}:
        await callback.message.answer("Кнопка устарела. Откройте запись заново.")
        return
    delta = int(raw_delta)
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        log = await crud.sleep_by_id(session, user.child_id, int(raw_id)) if user else None
        if not user or not log or log.child_id != user.child_id:
            await callback.message.answer("Запись недоступна.")
            return
        old_value = log.start_time if field == "start" else log.end_time
        if old_value is None:
            await callback.message.answer("Время ещё не записано.")
            return
        new_value = old_value + timedelta(minutes=delta)
        if new_value > utc_now() + timedelta(minutes=2):
            await callback.message.answer("Нельзя указать время в будущем.")
            return
        candidate_start = new_value if field == "start" else log.start_time
        candidate_end = new_value if field == "end" else log.end_time
        if candidate_end is not None and candidate_end - candidate_start >= timedelta(hours=24):
            await callback.message.answer("Интервал сна не может длиться 24 часа или больше.")
            return
        updated, status = await crud.update_sleep_interval(
            session,
            user.child_id,
            log.id,
            candidate_start,
            candidate_end,
            user.child.timezone,
            edited_by_user_id=user.id,
        )
        if status != "updated":
            detail = (
                "Новое время пересекается с другой записью сна."
                if status in {"overlap", "duplicate"}
                else "Начало должно быть раньше окончания."
            )
            await callback.message.answer(detail)
            return
        child_id, timezone, author = user.child_id, user.child.timezone, user.display_name
    await resync_child_runtime(bot, child_id)
    sign = "+" if delta > 0 else ""
    await callback.message.answer(
        f"✏️ Время изменено на {sign}{delta} мин: {to_local(new_value, timezone):%H:%M}.\n"
        f"<i>Изменил(а): {author}</i>"
    )


@router.message(EditTime.value, F.text)
async def edit_time_save(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await state.clear()
            return
        log = await crud.sleep_by_id(session, user.child_id, data["log_id"])
        if log is None:
            anchor_value = utc_now()
        elif data["field"] == "start":
            anchor_value = log.start_time
        else:
            anchor_value = log.end_time or utc_now()
        anchor_date = to_local(anchor_value, user.child.timezone).date()
        value = parse_anchored_local_time(message.text, anchor_date, user.child.timezone)
        if value is None:
            value = parse_relative_time(message.text, user.child.timezone)
        if not value:
            await message.answer("Не понял время. Пример: 14:15 или «20 минут назад».")
            return
        if not log or log.child_id != user.child_id:
            await state.clear()
            await message.answer("Запись не найдена.")
            return
        if value > utc_now() + timedelta(minutes=2):
            await message.answer("Нельзя указать время в будущем.")
            return
        candidate_start = value if data["field"] == "start" else log.start_time
        candidate_end = value if data["field"] == "end" else log.end_time
        if candidate_end is not None and candidate_end - candidate_start >= timedelta(hours=24):
            await message.answer("Интервал сна не может длиться 24 часа или больше.")
            return
        updated, status = await crud.update_sleep_interval(
            session,
            user.child_id,
            log.id,
            candidate_start,
            candidate_end,
            user.child.timezone,
            expected_start=data.get("expected_start"),
            expected_end=data.get("expected_end"),
            check_expected="expected_start" in data,
            edited_by_user_id=user.id,
        )
        if status == "stale":
            await state.clear()
            await message.answer("Запись уже изменил другой родитель. Откройте историю заново.")
            return
        if status != "updated":
            detail = (
                "Новое время пересекается с другой записью сна."
                if status in {"overlap", "duplicate"}
                else "Проверьте начало и окончание сна."
            )
            await message.answer(detail)
            return
        child_id, timezone = user.child_id, user.child.timezone
    await state.clear()
    await resync_child_runtime(bot, child_id)
    await message.answer(f"Время исправлено на {to_local(value, timezone):%H:%M}.")


@router.message(F.text.regexp(r"(?i)^(уснул|уснула|заснул|проснулся|проснулась)"))
async def natural_time(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        parsed = parse_relative_time(message.text, user.child.timezone)
    if not parsed:
        await message.answer("Не понял время. Примеры: «уснул в 14:15», «проснулся 20 минут назад».")
        return
    if message.text.lower().startswith(("проснулся", "проснулась")):
        await do_wake(message, parsed)
    else:
        await do_sleep_start(message, parsed)
