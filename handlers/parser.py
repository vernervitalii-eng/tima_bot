from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from database import crud
from database.session import db_session
from parser import parse_text
from services.time_utils import local_to_utc, to_local, utc_now
from handlers.sleep import do_sleep_start, do_wake


router = Router(name="parser")


@router.message(F.text, ~F.text.startswith("/"))
async def parse_natural_message(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        parsed = parse_text(message.text, to_local(utc_now(), user.child.timezone).date())

    # Одиночные короткие фразы используют привычную live-логику уведомлений.
    if len(parsed.point_events) == 1:
        kind, at, _ = parsed.point_events[0]
        if kind == "sleep_start" and len(parsed.sleeps) == 1 and parsed.sleeps[0].end is None:
            await do_sleep_start(message, local_to_utc(at, user.child.timezone))
            return
        if kind == "wake" and not parsed.sleeps:
            await do_wake(message, local_to_utc(at, user.child.timezone))
            return

    if not parsed.sleeps:
        await message.answer(
            "Не нашёл событие сна. Примеры: «04.08 10:15-11:30 спал», "
            "«уснул в 21:00», «проснулся в 07:30»."
        )
        return

    sleeps = [
        item.__class__(
            start=local_to_utc(item.start, user.child.timezone),
            end=local_to_utc(item.end, user.child.timezone) if item.end else None,
            sleep_type=item.sleep_type,
            duration_minutes=item.duration_minutes,
            wake_before_minutes=item.wake_before_minutes,
            source_line=item.source_line,
        )
        for item in parsed.sleeps
    ]
    async with db_session() as session:
        current = await crud.get_user(session, message.from_user.id)
        if not current:
            await message.answer("Сначала выполните /start.")
            return
        stats = await crud.seed_monthly_data(session, current.child_id, current.id, sleeps)

    warning = f"\n⚠️ Предупреждений парсера: {len(parsed.warnings)}" if parsed.warnings else ""
    await message.answer(
        "✅ События распознаны и сохранены.\n"
        f"💤 Снов добавлено: {stats['sleep_added']}\n"
        f"↩️ Дубликатов пропущено: {stats['sleep_skipped']}"
        + warning
    )
