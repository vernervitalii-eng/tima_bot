from datetime import timedelta

from aiogram import F, Router
from aiogram.types import Message

from database import crud
from database.session import db_session
from services.norms import norm_for_age, wake_recommendation
from services.time_utils import age_parts, format_duration, is_quiet_hours, local_day_start_utc, utc_now

router = Router(name="statistics")


@router.message(F.text == "📊 Статистика")
async def statistics(message: Message) -> None:
    now = utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        start = local_day_start_utc(user.child.timezone, now)
        logs = await crud.sleeps_overlapping(session, user.child_id, start, now)
        active = await crud.active_sleep(session, user.child_id)
        last = await crud.last_completed_sleep(session, user.child_id)
        months, days = age_parts(user.child.birth_date)
        norm = norm_for_age(months)

        day_sleep = timedelta()
        day_count = 0
        total_sleep = timedelta()
        for log in logs:
            end = min(log.end_time or now, now)
            begin = max(log.start_time, start)
            duration = max(end - begin, timedelta())
            total_sleep += duration
            if log.sleep_type == "day":
                day_sleep += duration
                day_count += 1

        wake_duration = timedelta() if active else (now - last.end_time if last and last.end_time else None)
        wake_minutes = int(wake_duration.total_seconds() // 60) if wake_duration is not None else 0
        recommendation = (
            "Сейчас ребёнок спит — окно бодрствования начнётся после пробуждения."
            if active else wake_recommendation(wake_minutes, norm)
        )
        sleep_hours = total_sleep.total_seconds() / 3600
        if sleep_hours and sleep_hours < norm.sleep_min:
            sleep_comparison = "пока ниже суточного ориентира"
        elif sleep_hours > norm.sleep_max:
            sleep_comparison = "выше суточного ориентира"
        else:
            sleep_comparison = "в пределах суточного ориентира"

        text = (
            f"<b>Сегодня · {user.child.name}</b>\n\n"
            f"• Дневной сон: <code>{format_duration(day_sleep)}</code>\n"
            f"• Дневных снов: <code>{day_count}</code>\n"
            f"• Всего сна: <code>{format_duration(total_sleep)}</code> ({sleep_comparison})\n"
            f"• Сейчас: {'ребёнок спит' if active else f'<code>{format_duration(wake_duration)}</code> бодрствует'}\n\n"
            f"Возраст: {months} мес. {days} дн.\n"
            f"Ориентир бодрствования: <code>{format_duration(timedelta(minutes=norm.wake_min))} – "
            f"{format_duration(timedelta(minutes=norm.wake_max))}</code>\n"
            f"Сон за сутки: <code>{norm.sleep_min:g}–{norm.sleep_max:g}ч</code>\n\n"
            f"{recommendation}"
        )
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)
    await message.answer(text, disable_notification=silent)


@router.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer("Выберите действие на клавиатуре или выполните /start.")
