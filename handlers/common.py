from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from database import crud
from database.session import db_session
from handlers.states import Onboarding
from keyboards.main import main_keyboard
from keyboards.inline import start_choice_keyboard
from services.live_status import build_live_status_view
from services.subscription import BRAND_NAME, premium_status_text
from services.time_utils import age_parts, parse_birth_date, to_local

router = Router(name="common")


WELCOME_CARD = (
    "━━━━━━━━━━━━━━━━━━━━\n"
    f"👶 <b>{BRAND_NAME} • Персональный консультант по сну</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Добро пожаловать! Этот бот помогает родителям наладить стабильный режим "
    "сна ребёнка без слёз и переутомлений.\n\n"
    "✨ <b>Что умеет бот:</b>\n"
    "• 💤 <b>Умный трекинг:</b> сон и подъём в один клик или текстом.\n"
    "• ⏱ <b>Контроль бодрствования:</b> расчёт ВБ и длительности.\n"
    "• 🎯 <b>Смарт-окна сна:</b> прогноз следующего укладывания.\n"
    "• 🧠 <b>AI-Сомнолог:</b> анализ биоритмов и график дня.\n"
    "• 💬 <b>Диалог с экспертом:</b> вопросы и корректировка режима.\n"
    "• 📊 <b>Отчёты:</b> диаграммы и хронология суток.\n\n"
    "🎁 <b>Вам доступно 3 дня полного Premium-доступа!</b>\n"
    "Триал начнётся автоматически после создания или подключения профиля.\n"
    "━━━━━━━━━━━━━━━━━━━━"
)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if user:
            months, days = age_parts(user.child.birth_date)
            view = await build_live_status_view(session, user.child)
            await message.answer(
                f"👶 <b>{BRAND_NAME}</b>\n"
                f"С возвращением! {user.child.name}: {months} мес. {days} дн.\n"
                f"{premium_status_text(user, timezone_name=user.child.timezone)}\n\n"
                f"{view.text}",
                reply_markup=main_keyboard(view.is_sleeping),
                disable_notification=view.silent,
            )
            return
    await message.answer(
        WELCOME_CARD,
        reply_markup=main_keyboard(None),
    )
    await message.answer(
        "Для сохранения данных сначала создайте семейный профиль или подключитесь к существующему:",
        reply_markup=start_choice_keyboard(),
    )


@router.callback_query(F.data == "onboarding:create")
async def onboarding_create(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    async with db_session() as session:
        if await crud.get_user(session, callback.from_user.id):
            await callback.message.answer("Профиль уже существует.")
            return
    await state.set_state(Onboarding.name)
    await callback.message.answer("Как зовут ребёнка? Напишите имя.")


@router.message(Onboarding.name, F.text, ~F.text.startswith("/"))
async def onboarding_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 1 <= len(name) <= 80:
        await message.answer("Введите имя длиной от 1 до 80 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(Onboarding.birth_date)
    await message.answer("Введите дату рождения в формате ДД.ММ.ГГГГ, например 15.03.2025.")


@router.message(Onboarding.birth_date, F.text, ~F.text.startswith("/"))
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
        trial_end = user.trial_end_date
    await state.clear()
    months, days = age_parts(birth_date)
    await message.answer(
        f"Готово! Профиль {child_name} создан. Возраст: {months} мес. {days} дн.\n\n"
        f"🎁 Premium-триал активен до: "
        f"<code>{to_local(trial_end, settings.timezone):%d.%m.%Y %H:%M}</code>\n\n"
        f"Семейный код: <code>{code}</code>\nДругой взрослый может выполнить: <code>/join {code}</code>",
        reply_markup=main_keyboard(False),
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if user is None:
            await message.answer("Действие отменено. Для начала работы выполните /start.")
            return
        view = await build_live_status_view(session, user.child)
    await message.answer(
        f"↩️ <b>Действие отменено</b>\n\n{view.text}",
        reply_markup=main_keyboard(view.is_sleeping),
        disable_notification=view.silent,
    )
