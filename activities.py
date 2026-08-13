from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from database.session import db_session
from handlers.states import NoteActivity
from keyboards.inline import activity_keyboard
from services.scheduler import notify_family
from services.time_utils import is_quiet_hours, to_local, utc_now

router = Router(name="activities")


@router.message(F.text == "🍼 Активность")
async def activity_menu(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)
    await message.answer("Что отметить?", reply_markup=activity_keyboard(), disable_notification=silent)


@router.callback_query(F.data.startswith("activity:"))
async def activity_click(callback: CallbackQuery, state: FSMContext) -> None:
    _, activity_type, details = callback.data.split(":", 2)
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала выполните /start", show_alert=True)
            return
        if activity_type == "notes":
            await state.set_state(NoteActivity.value)
            await callback.message.answer("Напишите короткую заметку о лекарстве или зубах. /cancel — отмена.")
            await callback.answer()
            return
        now = utc_now()
        await crud.add_activity(session, user.child_id, activity_type, now, details, user.id)
        local_time = to_local(now, user.child.timezone)
        child_id, author = user.child_id, user.display_name
    labels = {"feeding": f"Кормление ({details})", "diaper": "Подгузник"}
    await callback.answer("Записано")
    await notify_family(
        callback.bot,
        child_id,
        f"✅ {labels[activity_type]} — {local_time:%H:%M}.\n\n<i>Запись добавил(а): {author}</i>",
    )


@router.message(NoteActivity.value, F.text)
async def activity_note(message: Message, state: FSMContext) -> None:
    details = message.text.strip()
    if not details or len(details) > 1000:
        await message.answer("Заметка должна содержать от 1 до 1000 символов.")
        return
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await state.clear()
            return
        now = utc_now()
        await crud.add_activity(session, user.child_id, "notes", now, details, user.id)
        local_time = to_local(now, user.child.timezone)
        child_id, author = user.child_id, user.display_name
    await state.clear()
    await notify_family(
        message.bot, child_id,
        f"✅ Заметка сохранена — {local_time:%H:%M}.\n{details}\n\n<i>Запись добавил(а): {author}</i>",
    )
