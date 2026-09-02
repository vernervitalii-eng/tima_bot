from __future__ import annotations

from datetime import timedelta
from html import escape
from math import ceil

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database import crud
from database.models import SleepLog, SleepType
from database.session import db_session
from handlers.states import AddMissedSleep, HistorySleepEdit
from keyboards.inline import (
    history_add_confirmation_keyboard,
    history_delete_confirmation_keyboard,
    history_edit_confirmation_keyboard,
    history_edit_keyboard,
    history_keyboard,
)
from parser import parse_text
from services.live_status import resync_child_runtime
from services.time_utils import (
    format_duration,
    local_to_utc,
    parse_anchored_local_time,
    to_local,
    utc_now,
)


router = Router(name="history")
PAGE_SIZE = 10


def _record_text(log: SleepLog, position: int | None, timezone_name: str) -> str:
    now = utc_now()
    local_start = to_local(log.start_time, timezone_name)
    local_end = to_local(log.end_time, timezone_name) if log.end_time else None
    end_label = (
        local_end.strftime("%H:%M")
        if local_end and local_end.date() == local_start.date()
        else local_end.strftime("%d.%m %H:%M") if local_end else "сейчас"
    )
    duration = (log.end_time or now) - log.start_time
    kind = "Ночной сон" if log.sleep_type == SleepType.NIGHT.value else "Дневной сон"
    if log.end_time is None:
        kind = "Сон продолжается"
    prefix = f"{position}." if position is not None else "•"
    return (
        f"{prefix} <code>{local_start:%d.%m %H:%M} – {end_label}</code> · {kind} "
        f"<i>(<code>{format_duration(duration)}</code>)</i>"
    )


def _interval_label(start, end, timezone_name: str) -> str:
    local_start = to_local(start, timezone_name)
    if end is None:
        return f"{local_start:%d.%m.%Y %H:%M} — сейчас"
    local_end = to_local(end, timezone_name)
    return f"{local_start:%d.%m.%Y %H:%M} — {local_end:%d.%m.%Y %H:%M}"


def _add_sleep_prompt() -> str:
    return (
        "<b>Добавить пропущенный сон</b>\n\n"
        "Отправьте дату и интервал одним сообщением:\n"
        "<code>31.08.2026 10:15-11:30</code>\n"
        "или <code>вчера 21:00-07:30</code>\n\n"
        "Перед сохранением я покажу запись для проверки. /cancel — отмена."
    )


async def _history_payload(
    telegram_id: int,
    requested_page: int,
) -> tuple[str, InlineKeyboardMarkup | None, int] | None:
    async with db_session() as session:
        user = await crud.get_user(session, telegram_id)
        if not user:
            return None
        logs, total = await crud.sleep_history_page(session, user.child_id, requested_page, PAGE_SIZE)
        total_pages = max(ceil(total / PAGE_SIZE), 1)
        page = min(max(requested_page, 0), total_pages - 1)
        if page != requested_page:
            logs, total = await crud.sleep_history_page(session, user.child_id, page, PAGE_SIZE)
        timezone_name = user.child.timezone
        child_name = user.child.name

    if logs:
        blocks = [
            _record_text(log, page * PAGE_SIZE + index, timezone_name)
            for index, log in enumerate(logs, start=1)
        ]
        body = "\n\n".join(blocks)
        markup = history_keyboard([log.id for log in logs], page, total_pages)
    else:
        body = "<i>Записей сна пока нет.</i>"
        markup = history_keyboard([], page, total_pages)
    text = (
        f"📋 <b>История сна · {escape(child_name)}</b>\n\n"
        f"{body}\n\n"
        f"<i>Страница {page + 1} из {total_pages} • всего записей: {total}</i>"
    )
    return text, markup, page


async def _edit_history(message: Message, telegram_id: int, page: int) -> None:
    payload = await _history_payload(telegram_id, page)
    if payload is None:
        await message.edit_text("Сначала выполните /start.")
        return
    text, markup, _ = payload
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@router.message(Command("history"))
@router.message(Command("list"))
@router.message(F.text.in_({"📋 История", "📋 История записей"}))
async def history_view(message: Message, state: FSMContext) -> None:
    await state.clear()
    payload = await _history_payload(message.from_user.id, 0)
    if payload is None:
        await message.answer("Сначала выполните /start.")
        return
    text, markup, _ = payload
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("history:page:"))
async def history_page(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    page = max(int(callback.data.rsplit(":", 1)[1]), 0)
    await _edit_history(callback.message, callback.from_user.id, page)


@router.callback_query(F.data.regexp(r"^history:edit:\d+:\d+$"))
async def history_edit_open(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    _, _, raw_id, raw_page = callback.data.split(":")
    log_id, page = int(raw_id), int(raw_page)
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        log = await crud.sleep_by_id(session, user.child_id, log_id) if user else None
        if not user or not log:
            await callback.message.answer("Запись не найдена или недоступна.")
            return
        preview = _record_text(log, None, user.child.timezone)
    await callback.message.edit_text(
        "<b>Изменить запись</b>\n\n"
        f"{preview}\n\n"
        "Что именно нужно изменить?",
        reply_markup=history_edit_keyboard(log_id, page),
    )


@router.callback_query(F.data.startswith("history:field:"))
async def history_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, _, field, raw_id, raw_page = callback.data.split(":")
    log_id, page = int(raw_id), int(raw_page)
    if field not in {"start", "end"}:
        await callback.message.answer("Неизвестное поле записи.")
        return
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        log = await crud.sleep_by_id(session, user.child_id, log_id) if user else None
        if not user or not log:
            await callback.message.answer("Запись не найдена или недоступна.")
            return
        timezone_name = user.child.timezone
        current_value = log.start_time if field == "start" else log.end_time
        anchor_value = current_value or (utc_now() if field == "end" else log.start_time)
        anchor_date = to_local(anchor_value, timezone_name).date()
        current_label = (
            to_local(current_value, timezone_name).strftime("%d.%m.%Y %H:%M")
            if current_value is not None else "не указано"
        )
        expected_start, expected_end = log.start_time, log.end_time

    await state.clear()
    await state.set_state(HistorySleepEdit.value)
    await state.update_data(
        log_id=log_id,
        page=page,
        field=field,
        anchor_date=anchor_date,
        timezone_name=timezone_name,
        expected_start=expected_start,
        expected_end=expected_end,
    )
    field_name = "начала сна" if field == "start" else "пробуждения"
    await callback.message.answer(
        f"🕒 Текущее время {field_name}: <code>{current_label}</code>\n\n"
        "Введите новое время как <code>ЧЧ:ММ</code> — дата записи сохранится.\n"
        "Для смены даты: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>.\n"
        "/cancel — отмена."
    )


@router.message(HistorySleepEdit.value, F.text, ~F.text.startswith("/"))
async def history_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    value = parse_anchored_local_time(
        message.text,
        data["anchor_date"],
        data["timezone_name"],
    )
    if value is None:
        await message.answer(
            "Не понял время. Введите, например, <code>14:15</code> или "
            "<code>31.08.2026 14:15</code>."
        )
        return
    if value > utc_now() + timedelta(minutes=2):
        await message.answer("Нельзя указать время в будущем.")
        return

    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        log = await crud.sleep_by_id(session, user.child_id, data["log_id"]) if user else None
        if not user or not log:
            await state.clear()
            await message.answer("Запись больше не существует.")
            return
        if log.start_time != data["expected_start"] or log.end_time != data["expected_end"]:
            await state.clear()
            await message.answer(
                "⚠️ Эту запись уже изменил другой родитель. Откройте историю заново."
            )
            return
        candidate_start = value if data["field"] == "start" else log.start_time
        candidate_end = value if data["field"] == "end" else log.end_time
        if candidate_end is not None and candidate_end <= candidate_start:
            await message.answer("Окончание сна должно быть позже начала.")
            return
        if candidate_end is not None and candidate_end - candidate_start >= timedelta(hours=24):
            await message.answer("Интервал сна не может длиться 24 часа или больше. Проверьте дату.")
            return
        conflict = await crud.sleep_interval_conflict(
            session,
            user.child_id,
            candidate_start,
            candidate_end,
            exclude_log_id=log.id,
        )
        if conflict is not None:
            await message.answer(
                "⚠️ Новое время пересекается с другой записью сна. "
                "Введите время ещё раз."
            )
            return
        timezone_name = user.child.timezone

    await state.update_data(candidate_start=candidate_start, candidate_end=candidate_end)
    await state.set_state(HistorySleepEdit.confirm)
    duration = (
        format_duration(candidate_end - candidate_start)
        if candidate_end is not None else "сон продолжается"
    )
    await message.answer(
        "<b>Проверьте изменение</b>\n\n"
        f"Было: <code>{_interval_label(data['expected_start'], data['expected_end'], timezone_name)}</code>\n"
        f"Будет: <code>{_interval_label(candidate_start, candidate_end, timezone_name)}</code>\n"
        f"Длительность: <code>{duration}</code>",
        reply_markup=history_edit_confirmation_keyboard(),
    )


@router.callback_query(F.data == "history:edit:save")
async def history_edit_save(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if "candidate_start" not in data:
        await callback.message.answer("Срок подтверждения истёк. Откройте запись заново.")
        return
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await state.clear()
            await callback.message.answer("Сначала выполните /start.")
            return
        log, status = await crud.update_sleep_interval(
            session,
            user.child_id,
            data["log_id"],
            data["candidate_start"],
            data["candidate_end"],
            user.child.timezone,
            expected_start=data["expected_start"],
            expected_end=data["expected_end"],
            check_expected=True,
            edited_by_user_id=user.id,
        )
        child_id = user.child_id
        timezone_name = user.child.timezone

    if status != "updated":
        if status == "stale":
            await state.clear()
            await callback.message.edit_text(
                "⚠️ Запись уже изменил другой родитель. Откройте историю заново."
            )
            return
        if status in {"overlap", "duplicate"}:
            await state.set_state(HistorySleepEdit.value)
            await callback.message.edit_text(
                "⚠️ Время пересекается с другой записью. Введите другое время или /cancel."
            )
            return
        await state.clear()
        await callback.message.edit_text("Не удалось изменить запись. Откройте историю заново.")
        return

    await state.clear()
    await callback.message.answer(
        "✅ Запись исправлена: "
        f"<code>{_interval_label(log.start_time, log.end_time, timezone_name)}</code>."
    )
    await _edit_history(callback.message, callback.from_user.id, data["page"])
    await resync_child_runtime(callback.bot, child_id)


@router.callback_query(F.data == "history:edit:cancel")
async def history_edit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    page = int(data.get("page", 0))
    await state.clear()
    await _edit_history(callback.message, callback.from_user.id, page)


@router.callback_query(F.data.regexp(r"^history:add:\d+$"))
async def history_add_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    page = max(int(callback.data.rsplit(":", 1)[1]), 0)
    async with db_session() as session:
        if await crud.get_user(session, callback.from_user.id) is None:
            await callback.message.answer("Сначала выполните /start.")
            return
    await state.clear()
    await state.set_state(AddMissedSleep.interval)
    await state.update_data(page=page)
    await callback.message.answer(_add_sleep_prompt())


@router.message(Command("add_sleep"))
async def history_add_command(message: Message, state: FSMContext) -> None:
    async with db_session() as session:
        if await crud.get_user(session, message.from_user.id) is None:
            await message.answer("Сначала выполните /start.")
            return
    await state.clear()
    await state.set_state(AddMissedSleep.interval)
    await state.update_data(page=0)
    await message.answer(_add_sleep_prompt())


@router.message(AddMissedSleep.interval, F.text, ~F.text.startswith("/"))
async def history_add_interval(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await state.clear()
            await message.answer("Сначала выполните /start.")
            return
        timezone_name = user.child.timezone
        reference_date = to_local(utc_now(), timezone_name).date()
        parsed = parse_text(f"{message.text} спал", reference_date)
        if parsed.warnings:
            await message.answer(
                "Не получилось проверить дату или последовательность времени. "
                "Введите интервал полностью, например: "
                "<code>31.08.2026 10:15-11:30</code>."
            )
            return
        if len(parsed.sleeps) != 1 or parsed.sleeps[0].end is None:
            await message.answer(
                "Не нашёл один полный интервал. Пример: "
                "<code>31.08.2026 10:15-11:30</code>."
            )
            return
        parsed_sleep = parsed.sleeps[0]
        start = local_to_utc(parsed_sleep.start, timezone_name)
        end = local_to_utc(parsed_sleep.end, timezone_name)
        if end <= start:
            await message.answer("Окончание должно быть позже начала сна.")
            return
        if end > utc_now() + timedelta(minutes=2):
            await message.answer("Нельзя добавить сон, который заканчивается в будущем.")
            return
        if end - start >= timedelta(hours=24):
            await message.answer("Сон не может длиться 24 часа или больше. Проверьте дату.")
            return
        conflict = await crud.sleep_interval_conflict(session, user.child_id, start, end)
        if conflict is not None:
            await message.answer(
                "⚠️ Этот интервал совпадает или пересекается с уже сохранённым сном. "
                "Проверьте историю и введите другое время."
            )
            return

    await state.update_data(start=start, end=end, timezone_name=timezone_name)
    await state.set_state(AddMissedSleep.confirm)
    await message.answer(
        "<b>Проверьте новую запись</b>\n\n"
        f"Сон: <code>{_interval_label(start, end, timezone_name)}</code>\n"
        f"Длительность: <code>{format_duration(end - start)}</code>\n\n"
        "Сохранить эту запись?",
        reply_markup=history_add_confirmation_keyboard(),
    )


@router.callback_query(F.data == "history:add:save")
async def history_add_save(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if "start" not in data or "end" not in data:
        await callback.message.answer("Срок подтверждения истёк. Начните добавление заново.")
        return
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await state.clear()
            await callback.message.answer("Сначала выполните /start.")
            return
        log, status = await crud.create_sleep_interval(
            session,
            user.child_id,
            user.id,
            data["start"],
            data["end"],
            user.child.timezone,
        )
        child_id = user.child_id
        timezone_name = user.child.timezone

    if status != "created":
        if status == "duplicate":
            await state.clear()
            await callback.message.answer("ℹ️ Такая запись уже существует — дубль не добавлен.")
            await _edit_history(callback.message, callback.from_user.id, data.get("page", 0))
            return
        await state.set_state(AddMissedSleep.interval)
        await callback.message.edit_text(
            "⚠️ Интервал пересекается с записью, которую недавно добавил другой родитель. "
            "Отправьте другое время или /cancel."
        )
        return

    await state.clear()
    await callback.message.answer(
        "✅ Пропущенный сон добавлен: "
        f"<code>{_interval_label(log.start_time, log.end_time, timezone_name)}</code>."
    )
    await _edit_history(callback.message, callback.from_user.id, 0)
    await resync_child_runtime(callback.bot, child_id)


@router.callback_query(F.data == "history:add:retry")
async def history_add_retry(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    page = int(data.get("page", 0))
    await state.clear()
    await state.set_state(AddMissedSleep.interval)
    await state.update_data(page=page)
    await callback.message.edit_text(_add_sleep_prompt())


@router.callback_query(F.data == "history:add:cancel")
async def history_add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    page = int(data.get("page", 0))
    await state.clear()
    await _edit_history(callback.message, callback.from_user.id, page)


@router.callback_query(F.data.startswith("history:delete:"))
async def history_delete_prompt(callback: CallbackQuery) -> None:
    await callback.answer()
    _, _, raw_id, raw_page = callback.data.split(":")
    log_id, page = int(raw_id), int(raw_page)
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        log = await crud.sleep_by_id(session, user.child_id, log_id) if user else None
        if not user or not log:
            await callback.message.answer("Запись уже удалена или недоступна.")
            return
        preview = _record_text(log, None, user.child.timezone)
    await callback.message.edit_text(
        "<b>Удалить эту запись?</b>\n\n"
        f"{preview}\n\n"
        "Это действие удалит только выбранный сон и не затронет остальные данные.",
        reply_markup=history_delete_confirmation_keyboard(log_id, page),
    )


@router.callback_query(F.data.startswith("history:cancel:"))
async def history_delete_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
    page = max(int(callback.data.rsplit(":", 1)[1]), 0)
    await _edit_history(callback.message, callback.from_user.id, page)


@router.callback_query(F.data.startswith("history:confirm:"))
async def history_delete_confirm(callback: CallbackQuery) -> None:
    await callback.answer()
    _, _, raw_id, raw_page = callback.data.split(":")
    log_id, page = int(raw_id), int(raw_page)
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Сначала выполните /start.")
            return
        deleted = await crud.delete_sleep_log(session, user.child_id, log_id)
        child_id = user.child_id

    if not deleted:
        await callback.message.answer("Запись уже удалена.")
    await _edit_history(callback.message, callback.from_user.id, page)
    if deleted:
        await resync_child_runtime(callback.bot, child_id)
