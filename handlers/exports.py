from __future__ import annotations

import csv
import io
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from database import crud
from database.session import db_session
from keyboards.inline import export_keyboard
from services.time_utils import is_quiet_hours, to_local, utc_now

router = Router(name="exports")


@router.callback_query(F.data == "export:menu")
async def export_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        silent = bool(user and user.child.silent_mode and is_quiet_hours(user.child.timezone))
    await callback.message.answer(
        "Выберите период. CSV открывается в Excel, Numbers и Google Sheets.",
        reply_markup=export_keyboard(), disable_notification=silent,
    )


@router.callback_query(F.data.regexp(r"^export:csv:(7|30)$"))
async def export_csv(callback: CallbackQuery) -> None:
    await callback.answer()
    days = int(callback.data.rsplit(":", 1)[1])
    now = utc_now()
    since = now - timedelta(days=days)
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.message.answer("Сначала выполните /start.")
            return
        sleeps = await crud.sleeps_overlapping(session, user.child_id, since, now)
        members = await crud.family_members(session, user.child_id)
        authors = {member.id: member.display_name for member in members}
        timezone = user.child.timezone
        child_name = user.child.name
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)

    # BOM + точка с запятой дают корректное открытие кириллицы в локальном Excel.
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "Категория", "Событие", "Начало (локальное)", "Окончание (локальное)",
        "Длительность, мин", "Автор начала", "Автор окончания",
    ])
    rows: list[tuple] = []
    for log in sleeps:
        end = log.end_time or now
        rows.append((
            log.start_time,
            "Сон",
            "Ночной сон" if log.sleep_type == "night" else "Дневной сон",
            to_local(log.start_time, timezone).strftime("%Y-%m-%d %H:%M"),
            to_local(log.end_time, timezone).strftime("%Y-%m-%d %H:%M") if log.end_time else "Продолжается",
            max(int((end - log.start_time).total_seconds() // 60), 0),
            authors.get(log.created_by_user_id, "Не указан"),
            authors.get(log.ended_by_user_id, "Не указан") if log.end_time else "",
        ))
    for row in sorted(rows, key=lambda item: item[0]):
        writer.writerow(row[1:])

    payload = ("\ufeff" + stream.getvalue()).encode("utf-8")
    safe_date = to_local(now, timezone).strftime("%Y-%m-%d")
    filename = f"sleep_{safe_date}_{days}d.csv"
    await callback.message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption=(
            f"История {child_name} за последние {days} дней. "
            "Время указано в локальном часовом поясе; длительность — в минутах."
        ),
        disable_notification=silent,
    )
