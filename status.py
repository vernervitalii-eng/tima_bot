from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from database import crud
from database.session import db_session
from services.time_utils import format_duration, is_quiet_hours, to_local, utc_now

router = Router(name="status")


@router.message(Command("status"))
@router.message(F.text == "📌 Текущий статус")
async def current_status(message: Message) -> None:
    now = utc_now()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        active = await crud.active_sleep(session, user.child_id)
        last_sleep = await crud.last_completed_sleep(session, user.child_id)
        feeding = await crud.latest_activity(session, user.child_id, "feeding")

        if active:
            creator = await crud.get_user_by_id(session, active.created_by_user_id)
            status_line = (
                f"💤 {user.child.name} спит уже {format_duration(now - active.start_time)} "
                f"(с {to_local(active.start_time, user.child.timezone):%H:%M}, "
                f"отметил(а): {creator.display_name if creator else 'член семьи'})."
            )
        elif last_sleep and last_sleep.end_time:
            status_line = f"☀️ {user.child.name} бодрствует уже {format_duration(now - last_sleep.end_time)}."
        else:
            status_line = f"☀️ {user.child.name} бодрствует. Время последнего пробуждения ещё не записано."

        if last_sleep and last_sleep.end_time:
            sleep_line = (
                f"Последний сон: {format_duration(last_sleep.end_time - last_sleep.start_time)} "
                f"(проснулся(ась) в {to_local(last_sleep.end_time, user.child.timezone):%H:%M})."
            )
        else:
            sleep_line = "Последний завершённый сон: нет данных."

        if feeding:
            author = await crud.get_user_by_id(session, feeding.created_by_user_id) if feeding.created_by_user_id else None
            feeding_line = (
                f"Последнее кормление: {format_duration(now - feeding.timestamp)} назад "
                f"({author.display_name if author else 'автор не указан'}, {feeding.details or 'тип не указан'})."
            )
        else:
            feeding_line = "Последнее кормление: нет данных."
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)

    await message.answer(
        f"📌 <b>Текущий статус</b>\n\n{status_line}\n{sleep_line}\n{feeding_line}",
        disable_notification=silent,
    )
