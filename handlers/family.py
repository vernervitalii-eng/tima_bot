from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from database import crud
from database.session import db_session
from database.models import Child
from keyboards.main import main_keyboard
from keyboards.inline import (
    family_keyboard,
    join_role_keyboard,
    reset_confirmation_keyboard,
    settings_keyboard,
    start_choice_keyboard,
)
from handlers.states import InviteFamily, JoinFamily
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
    await callback.answer()
    await state.clear()
    async with db_session() as session:
        if await crud.get_user(session, callback.from_user.id):
            await callback.message.answer("Вы уже состоите в семье.")
            return
    await state.set_state(JoinFamily.code)
    await callback.message.answer("Введите семейный код.")


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
    await callback.answer()
    name = callback.data.split(":", 1)[1]
    if name == "custom":
        await callback.message.answer("Напишите имя или роль, например «Дедушка».")
        return
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
        is_admin = user.role == "admin"
    await message.answer(text, reply_markup=family_keyboard(is_admin), disable_notification=silent)


@router.callback_query(F.data == "family:invite")
async def invite_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.message.answer("Сначала выполните /start.")
            return
        if user.role != "admin":
            await callback.message.answer("Приглашать участников может только администратор.")
            return
    await state.set_state(InviteFamily.telegram_id)
    await callback.message.answer(
        "Введите числовой Telegram ID пользователя.\n\n"
        "Узнать его можно, например, через бота @userinfobot. Для отмены отправьте /cancel."
    )


@router.message(InviteFamily.telegram_id, F.text, ~F.text.startswith("/"))
async def invite_by_telegram_id(message: Message, state: FSMContext, bot: Bot) -> None:
    raw_id = message.text.strip()
    if not raw_id.isascii() or not raw_id.isdigit():
        await message.answer("Нужен числовой Telegram ID без @username. Попробуйте ещё раз.")
        return
    telegram_id = int(raw_id)
    if telegram_id <= 0 or telegram_id > 9_223_372_036_854_775_807:
        await message.answer("Telegram ID выглядит неверно. Проверьте число и попробуйте ещё раз.")
        return
    if telegram_id == message.from_user.id:
        await message.answer("Это ваш собственный Telegram ID. Введите ID другого пользователя.")
        return

    async with db_session() as session:
        admin = await crud.get_user(session, message.from_user.id)
        if not admin or admin.role != "admin":
            await state.clear()
            await message.answer("Приглашение недоступно: профиль администратора не найден.")
            return
        child_name = admin.child.name
        _, status = await crud.invite_family_member(session, admin.child_id, telegram_id)

    await state.clear()
    if status == "already_member":
        await message.answer("Этот пользователь уже состоит в вашей семье.")
        return
    if status == "other_family":
        await message.answer(
            "Этот пользователь уже подключён к другой семье. Ему нужно сначала выполнить /reset в своём боте."
        )
        return

    try:
        await bot.send_message(
            telegram_id,
            f"Вас пригласили в семейный профиль «{child_name}». Откройте меню командой /start.",
        )
    except TelegramAPIError:
        await message.answer(
            "✅ Пользователь добавлен в семью, но сообщение пока не удалось доставить. "
            "Попросите пользователя открыть этого бота и нажать /start."
        )
    else:
        await message.answer("✅ Пользователь добавлен в семью, приглашение отправлено в Telegram.")


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
    await callback.answer()
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.message.answer("Сначала выполните /start.")
            return
        if user.role != "admin":
            await callback.message.answer("Изменять общие настройки может администратор.")
            return
        user.child.silent_mode = not user.child.silent_mode
        enabled = user.child.silent_mode
    await callback.message.edit_reply_markup(reply_markup=settings_keyboard(enabled, True))
    await callback.message.answer("Тихий режим включён." if enabled else "Тихий режим выключен.")


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
    await callback.answer()
    await state.clear()
    await show_reset_confirmation(callback.message, callback.from_user.id)


@router.callback_query(F.data == "confirm-reset:cancel")
async def reset_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("Сброс отменён.")


@router.callback_query(F.data.startswith("confirm-reset:"))
async def reset_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        if not user:
            await callback.message.answer("Профиль уже удалён.")
            return
        child_id = user.child_id
        child_name = user.child.name
        if action == "family":
            if user.role != "admin":
                await callback.message.answer("Только администратор может удалить семью.")
                return
            recipients = await crud.family_telegram_ids(session, child_id)
            await crud.delete_family(session, child_id)
        elif action == "leave":
            if user.role == "admin":
                await callback.message.answer("Администратор должен использовать полный сброс.")
                return
            recipients = [callback.from_user.id]
            await crud.leave_family(session, user)
        else:
            await callback.message.answer("Неизвестное действие.")
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
