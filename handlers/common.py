from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import Settings
from database import crud
from database.session import db_session
from handlers.states import Onboarding
from keyboards.main import main_keyboard
from services.time_utils import age_parts, parse_birth_date

router = Router(name="common")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if user:
            sleeping = await crud.active_sleep(session, user.child_id) is not None
            months, days = age_parts(user.child.birth_date)
            await message.answer(
                f"С возвращением! {user.child.name}: {months} мес. {days} дн.",
                reply_markup=main_keyboard(sleeping),
            )
            return
    await state.set_state(Onboarding.name)
    await message.answer("Как зовут ребёнка? Напишите имя.")


@router.message(Onboarding.name, F.text)
async def onboarding_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 1 <= len(name) <= 80:
        await message.answer("Введите имя длиной от 1 до 80 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(Onboarding.birth_date)
    await message.answer("Введите дату рождения в формате ДД.ММ.ГГГГ, например 15.03.2025.")


@router.message(Onboarding.birth_date, F.text)
async def onboarding_birth(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        birth_date = parse_birth_date(message.text)
    except (ValueError, TypeError):
        await message.answer("Не получилось распознать дату. Введите её как ДД.ММ.ГГГГ.")
        return
    data = await state.get_data()
    async with db_session() as session:
        existing = await crud.get_user(session, message.from_user.id)
        if existing:
            await state.clear()
            await message.answer("Профиль уже создан.", reply_markup=main_keyboard(False))
            return
        user = await crud.create_family(session, message.from_user.id, data["name"], birth_date, settings.timezone)
        code, child_name = user.child.invite_code, user.child.name
    await state.clear()
    months, days = age_parts(birth_date)
    await message.answer(
        f"Готово! Профиль {child_name} создан. Возраст: {months} мес. {days} дн.\n\n"
        f"Семейный код: <code>{code}</code>\nДругой взрослый может выполнить: <code>/join {code}</code>",
        reply_markup=main_keyboard(False),
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.")

