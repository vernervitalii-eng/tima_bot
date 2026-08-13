from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database import crud
from database.session import db_session
from database.models import Child
from keyboards.main import main_keyboard
from keyboards.inline import join_role_keyboard, settings_keyboard
from handlers.states import JoinFamily
from services.time_utils import age_parts, is_quiet_hours

router = Router(name="family")


@router.message(Command("join"))
async def join(message: Message, command: CommandObject, state: FSMContext) -> None:
    code = (command.args or "").strip()
    if not code:
        await message.answer("Использование: <code>/join СЕМЕЙНЫЙ_КОД</code>")
        return
    async with db_session() as session:
        existing = await crud.get_user(session, message.from_user.id)
        if existing:
            await message.answer("Вы уже привязаны к профилю ребёнка.")
            return
        child = await session.scalar(select(Child).where(Child.invite_code == code.upper()))
        if not child:
            await message.answer("Семейный код не найден. Проверьте его и попробуйте снова.")
            return
    await state.set_state(JoinFamily.display_name)
    await state.update_data(invite_code=code.upper())
    await message.answer("Как подписать вас в семье?", reply_markup=join_role_keyboard())


async def complete_join(message: Message, state: FSMContext, display_name: str, telegram_id: int) -> None:
    data = await state.get_data()
    async with db_session() as session:
        user = await crud.join_family(session, telegram_id, data["invite_code"], display_name)
        if not user:
            await state.clear()
            await message.answer("Семейный код больше не доступен.")
            return
        name = user.child.name
        sleeping = await crud.active_sleep(session, user.child_id) is not None
    await state.clear()
    await message.answer(
        f"Вы присоединились к профилю {name} как «{display_name}».",
        reply_markup=main_keyboard(sleeping),
    )


@router.callback_query(JoinFamily.display_name, F.data.startswith("join-role:"))
async def join_role_click(callback: CallbackQuery, state: FSMContext) -> None:
    name = callback.data.split(":", 1)[1]
    if name == "custom":
        await callback.message.answer("Напишите имя или роль, например «Дедушка».")
        await callback.answer()
        return
    await callback.answer()
    await complete_join(callback.message, state, name, callback.from_user.id)


@router.message(JoinFamily.display_name, F.text)
async def join_role_text(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 1 <= len(name) <= 80:
        await message.answer("Введите имя длиной от 1 до 80 символов.")
        return
    await complete_join(message, state, name, message.from_user.id)


@router.message(F.text == "👥 Семья")
async def family(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        members = await crud.family_members(session, user.child_id)
        admins = sum(member.role == "admin" for member in members)
        text = (
            f"👥 Профиль: {user.child.name}\nУчастников: {len(members)} (администраторов: {admins})\n\n"
            f"Семейный код: <code>{user.child.invite_code}</code>\n"
            f"Для подключения: <code>/join {user.child.invite_code}</code>"
        )
        silent = user.child.silent_mode and is_quiet_hours(user.child.timezone)
    await message.answer(text, disable_notification=silent)


@router.message(F.text == "⚙️ Настройки")
async def settings_view(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        months, days = age_parts(user.child.birth_date)
        await message.answer(
            f"⚙️ {user.child.name}\nДата рождения: {user.child.birth_date:%d.%m.%Y}\n"
            f"Возраст: {months} мес. {days} дн.\nЧасовой пояс: <code>{user.child.timezone}</code>\n\n"
            "Часовой пояс задаётся переменной BOT_TIMEZONE при создании профиля.",
            reply_markup=settings_keyboard(user.child.silent_mode),
            disable_notification=user.child.silent_mode and is_quiet_hours(user.child.timezone),
        )


@router.callback_query(F.data == "settings:toggle-silent")
async def toggle_silent(callback: CallbackQuery) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Сначала выполните /start", show_alert=True)
            return
        if user.role != "admin":
            await callback.answer("Изменять общие настройки может администратор", show_alert=True)
            return
        user.child.silent_mode = not user.child.silent_mode
        enabled = user.child.silent_mode
    await callback.message.edit_reply_markup(reply_markup=settings_keyboard(enabled))
    await callback.answer("Тихий режим включён" if enabled else "Тихий режим выключен")
