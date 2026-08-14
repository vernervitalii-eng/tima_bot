from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy import select

from database import crud
from database.session import db_session
from database.models import Child
from keyboards.main import main_keyboard
from keyboards.inline import (
    join_role_keyboard,
    reset_confirmation_keyboard,
    settings_keyboard,
    start_choice_keyboard,
)
from handlers.states import JoinFamily
from services.scheduler import cancel_child_jobs
from services.time_utils import age_parts, is_quiet_hours

router = Router(name="family")


@router.message(Command("join"))
async def join(message: Message, command: CommandObject, state: FSMContext) -> None:
    # Команда должна работать даже посреди создания нового профиля.
    await state.clear()
    code = (command.args or "").strip()
    if not code:
        await state.set_state(JoinFamily.code)
        await message.answer("Введите семейный код, который прислал администратор.")
        return
    await prepare_join(message, state, message.from_user.id, code)


async def prepare_join(message: Message, state: FSMContext, telegram_id: int, code: str) -> None:
    code = code.strip().upper()
    async with db_session() as session:
        existing = await crud.get_user(session, telegram_id)
        if existing:
            await message.answer(
                "Вы уже состоите в семье. Чтобы подключиться к другой, сначала используйте "
                "/reset или кнопку сброса в настройках."
            )
            return
        child = await session.scalar(select(Child).where(Child.invite_code == code))
        if not child:
            await message.answer("Семейный код не найден. Проверьте его и попробуйте снова.")
            await state.set_state(JoinFamily.code)
            return
    await state.set_state(JoinFamily.display_name)
    await state.update_data(invite_code=code)
    await message.answer("Как подписать вас в семье?", reply_markup=join_role_keyboard())


@router.callback_query(F.data == "onboarding:join")
async def onboarding_join(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with db_session() as session:
        if await crud.get_user(session, callback.from_user.id):
            await callback.answer("Вы уже состоите в семье", show_alert=True)
            return
    await state.set_state(JoinFamily.code)
    await callback.message.answer("Введите семейный код.")
    await callback.answer()


@router.message(JoinFamily.code, F.text, ~F.text.startswith("/"))
async def join_code_text(message: Message, state: FSMContext) -> None:
    await prepare_join(message, state, message.from_user.id, message.text)


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
            reply_markup=settings_keyboard(user.child.silent_mode, user.role == "admin"),
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
    await callback.message.edit_reply_markup(reply_markup=settings_keyboard(enabled, True))
    await callback.answer("Тихий режим включён" if enabled else "Тихий режим выключен")


async def show_reset_confirmation(message: Message, telegram_id: int) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, telegram_id)
        if not user:
            await message.answer("Профиль ещё не создан.", reply_markup=start_choice_keyboard())
            return
        is_admin = user.role == "admin"
        if is_admin:
            text = (
                "⚠️ <b>Полный сброс семьи</b>\n\n"
                "Будут безвозвратно удалены ребёнок, все записи сна, активности и подключения членов семьи."
            )
        else:
            text = "⚠️ Вы покинете семью. Данные ребёнка и записи остальных участников сохранятся."
    await message.answer(text, reply_markup=reset_confirmation_keyboard(is_admin))


@router.message(Command("reset"))
async def reset_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_reset_confirmation(message, message.from_user.id)


@router.callback_query(F.data == "settings:reset")
async def reset_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_reset_confirmation(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "confirm-reset:cancel")
async def reset_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Сброс отменён.")
    await callback.answer()


@router.callback_query(F.data.startswith("confirm-reset:"))
async def reset_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    action = callback.data.split(":", 1)[1]
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.answer("Профиль уже удалён", show_alert=True)
            return
        child_id = user.child_id
        child_name = user.child.name
        if action == "family":
            if user.role != "admin":
                await callback.answer("Только администратор может удалить семью", show_alert=True)
                return
            recipients = await crud.family_telegram_ids(session, child_id)
            await crud.delete_family(session, child_id)
        elif action == "leave":
            if user.role == "admin":
                await callback.answer("Администратор должен использовать полный сброс", show_alert=True)
                return
            recipients = [callback.from_user.id]
            await crud.leave_family(session, user)
        else:
            await callback.answer("Неизвестное действие", show_alert=True)
            return

    await state.clear()
    if action == "family":
        cancel_child_jobs(child_id)
        for telegram_id in recipients:
            if telegram_id == callback.from_user.id:
                continue
            try:
                await bot.send_message(
                    telegram_id,
                    f"Профиль семьи «{child_name}» удалён администратором.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                await bot.send_message(telegram_id, "Можно создать семью или подключиться снова.", reply_markup=start_choice_keyboard())
            except Exception:
                pass
        result_text = "Все данные семьи удалены."
    else:
        result_text = "Вы вышли из семьи. Семейные данные сохранены."

    await callback.message.answer(result_text, reply_markup=ReplyKeyboardRemove())
    await callback.message.answer("Что вы хотите сделать дальше?", reply_markup=start_choice_keyboard())
    await callback.answer("Готово")
