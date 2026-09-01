from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from database import crud
from database.session import db_session
from keyboards.main import main_keyboard
from services.live_status import build_live_status_view

router = Router(name="status")


@router.message(Command("status"))
@router.message(F.text == "📌 Текущий статус")
async def current_status(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        view = await build_live_status_view(session, user.child)

    await message.answer(
        view.text,
        reply_markup=main_keyboard(view.is_sleeping),
        disable_notification=view.silent,
    )
