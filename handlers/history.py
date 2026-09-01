from __future__ import annotations

from html import escape
from math import ceil

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database import crud
from database.models import SleepLog, SleepType
from database.session import db_session
from keyboards.inline import history_delete_confirmation_keyboard, history_keyboard
from services.scheduler import cancel_child_jobs, schedule_after_sleep, schedule_after_wake
from services.time_utils import format_duration, to_local, utc_now


router = Router(name="history")
PAGE_SIZE = 10


def _record_text(log: SleepLog, position: int, timezone_name: str) -> str:
    now = utc_now()
    local_start = to_local(log.start_time, timezone_name)
    local_end = to_local(log.end_time, timezone_name) if log.end_time else None
    end_label = (
        local_end.strftime("%H:%M")
        if local_end and local_end.date() == local_start.date()
        else local_end.strftime("%d.%m %H:%M") if local_end else "сейчас"
    )
    duration = (log.end_time or now) - log.start_time
    icon = "🌙" if log.sleep_type == SleepType.NIGHT.value else "💤"
    kind = "Ночной сон" if log.sleep_type == SleepType.NIGHT.value else "Дневной сон"
    if log.end_time is None:
        kind = "Сон продолжается"
    return (
        f"{position}. {icon} <code>{local_start:%d.%m %H:%M} — {end_label}</code>\n"
        f"   └ <i>{kind} • {format_duration(duration)}</i>"
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
        body = "🫧 <i>Записей сна пока нет.</i>"
        markup = None
    text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>ИСТОРИЯ СНА • {escape(child_name)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
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
@router.message(F.text == "📋 История записей")
async def history_view(message: Message) -> None:
    payload = await _history_payload(message.from_user.id, 0)
    if payload is None:
        await message.answer("Сначала выполните /start.")
        return
    text, markup, _ = payload
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("history:page:"))
async def history_page(callback: CallbackQuery) -> None:
    await callback.answer()
    page = max(int(callback.data.rsplit(":", 1)[1]), 0)
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
        preview = _record_text(log, 1, user.child.timezone)
    await callback.message.edit_text(
        "⚠️ <b>Удалить эту запись?</b>\n\n"
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
        active = await crud.active_sleep(session, user.child_id)
        last = await crud.last_completed_sleep(session, user.child_id)
        child_id = user.child_id
        birth_date = user.child.birth_date

    if not deleted:
        await callback.message.answer("Запись уже удалена.")
    cancel_child_jobs(child_id)
    if active:
        schedule_after_sleep(callback.bot, child_id, active.id, active.start_time)
    elif last and last.end_time:
        schedule_after_wake(callback.bot, child_id, birth_date, last.end_time)
    await _edit_history(callback.message, callback.from_user.id, page)
