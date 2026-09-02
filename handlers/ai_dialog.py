from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from config import Settings
from database import crud
from database.session import db_session
from handlers.states import AIState
from keyboards.inline import ai_dialog_exit_keyboard
from keyboards.main import main_keyboard
from services.ai_analyst import (
    GEMINI_OPERATION_TIMEOUT_SECONDS,
    ask_sleep_consultant,
    format_consultant_answer,
    get_last_base_routine,
    trim_dialog_history,
)
from services.live_status import build_live_status_view
from services.subscription import require_premium_access
from services.time_utils import age_parts, to_local, utc_now


logger = logging.getLogger(__name__)
router = Router(name="ai_dialog")

AI_DIALOG_GREETING = (
    "🧠 <b>Консультант по режиму</b>\n\n"
    "Я изучил всю историю снов и текущий график. Вы можете задать любой вопрос, "
    "внести замечания или попросить скорректировать режим:\n\n"
    "• <i>Почему в 14:00 он долго укладывается?</i>\n"
    "• <i>Давай сместим ночной сон на 21:00.</i>\n"
    "• <i>Как перестроить день после раннего подъёма?</i>\n\n"
    "Напишите вопрос или завершите консультацию кнопкой ниже."
)


async def _finish_dialog(
    message: Message,
    telegram_id: int,
    state: FSMContext,
) -> None:
    await state.clear()
    async with db_session() as session:
        user = await crud.get_user(session, telegram_id)
        if user is None:
            await message.answer("Диалог завершён. Для начала работы выполните /start.")
            return
        view = await build_live_status_view(session, user.child)
    await message.answer(
        f"Консультация завершена.\n\n{view.text}",
        reply_markup=main_keyboard(view.is_sleeping),
        disable_notification=view.silent,
    )


@router.message(Command("ask_ai"))
@router.message(F.text.in_({"💬 Консультант", "💬 Чат с ИИ-консультантом"}))
async def enter_ai_dialog(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await require_premium_access(message, message.from_user.id):
        return
    if not settings.gemini_api_key:
        await message.answer(
            "🧠 ИИ-консультант пока не настроен: отсутствует <code>GEMINI_API_KEY</code>."
        )
        return
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if user is None:
            await message.answer("Сначала выполните /start.")
            return
        snapshot = await crud.get_ai_routine_snapshot(session, user.child_id)
        child_id = user.child_id

    base_routine = snapshot.payload_json if snapshot else get_last_base_routine(child_id)
    await state.clear()
    await state.set_state(AIState.in_dialog)
    await state.update_data(
        ai_history=[],
        base_routine=base_routine,
        child_id=child_id,
        ai_busy=False,
    )
    await message.answer(
        "Режим консультации включён. Основное меню временно скрыто.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(AI_DIALOG_GREETING, reply_markup=ai_dialog_exit_keyboard())


@router.message(AIState.in_dialog, Command("cancel"))
async def cancel_ai_dialog(message: Message, state: FSMContext) -> None:
    await _finish_dialog(message, message.from_user.id, state)


@router.message(
    AIState.in_dialog,
    F.text.in_({"❌ Выйти из чата", "❌ Завершить диалог"}),
)
async def exit_ai_dialog_text(message: Message, state: FSMContext) -> None:
    await _finish_dialog(message, message.from_user.id, state)


@router.callback_query(F.data == "ai:dialog:exit")
async def exit_ai_dialog_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _finish_dialog(callback.message, callback.from_user.id, state)


@router.message(AIState.in_dialog, F.text, ~F.text.startswith("/"))
async def ai_dialog_question(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not await require_premium_access(message, message.from_user.id):
        await _finish_dialog(message, message.from_user.id, state)
        return
    question = (message.text or "").strip()
    if len(question) < 2:
        await message.answer("Напишите вопрос чуть подробнее.", reply_markup=ai_dialog_exit_keyboard())
        return
    if len(question) > 2000:
        await message.answer(
            "Сообщение слишком длинное. Сократите вопрос до 2000 символов.",
            reply_markup=ai_dialog_exit_keyboard(),
        )
        return

    state_data = await state.get_data()
    if state_data.get("ai_busy"):
        await message.answer("⏳ Я ещё обрабатываю предыдущий вопрос.")
        return
    await state.update_data(ai_busy=True)
    progress = await message.answer(
        "🧠 Анализирую вопрос и сверяю его с историей сна…",
        reply_markup=ai_dialog_exit_keyboard(),
    )

    try:
        now = utc_now()
        async with db_session() as session:
            user = await crud.get_user(session, message.from_user.id)
            if user is None:
                await state.clear()
                await progress.edit_text("Профиль не найден. Выполните /start.")
                return
            logs = await crud.sleeps_overlapping(
                session,
                user.child_id,
                now - timedelta(days=31),
                now + timedelta(minutes=1),
            )
            snapshot = await crud.get_ai_routine_snapshot(session, user.child_id)
            local_today = to_local(now, user.child.timezone).date()
            age_months, _ = age_parts(user.child.birth_date, local_today)
            timezone_name = user.child.timezone
            child_id = user.child_id

        dialog_history = trim_dialog_history(state_data.get("ai_history"))
        base_routine = (
            snapshot.payload_json
            if snapshot is not None
            else state_data.get("base_routine") or get_last_base_routine(child_id)
        )
        answer = await asyncio.wait_for(
            ask_sleep_consultant(
                settings.gemini_api_key,
                settings.gemini_model,
                age_months,
                timezone_name,
                logs,
                question,
                dialog_history,
                base_routine,
            ),
            timeout=GEMINI_OPERATION_TIMEOUT_SECONDS,
        )
        if await state.get_state() != AIState.in_dialog.state:
            try:
                await progress.delete()
            except Exception:
                pass
            return
        updated_history = trim_dialog_history(
            dialog_history
            + [
                {"role": "user", "text": question},
                {"role": "assistant", "text": answer},
            ]
        )
        await state.update_data(
            ai_history=updated_history,
            base_routine=base_routine,
            ai_busy=False,
        )
        await progress.edit_text(
            format_consultant_answer(answer),
            reply_markup=ai_dialog_exit_keyboard(),
        )
    except TimeoutError:
        logger.warning("Gemini не ответил за отведённое время в режиме диалога")
        if await state.get_state() == AIState.in_dialog.state:
            await state.update_data(ai_busy=False)
            await progress.edit_text(
                "<b>Ответ занимает слишком много времени</b>\n\n"
                "Запрос остановлен, поэтому бот снова готов принимать сообщения. "
                "Попробуйте повторить вопрос немного позже.",
                reply_markup=ai_dialog_exit_keyboard(),
            )
    except Exception:
        logger.exception("Ошибка диалога с Gemini")
        if await state.get_state() == AIState.in_dialog.state:
            await state.update_data(ai_busy=False)
            await progress.edit_text(
                "<b>ИИ-консультант временно недоступен</b>\n\n"
                "Состояние диалога сохранено. Попробуйте повторить вопрос немного позже или выйдите из чата.",
                reply_markup=ai_dialog_exit_keyboard(),
            )


@router.message(AIState.in_dialog, F.text.startswith("/"))
async def ai_dialog_command_guard(message: Message, state: FSMContext) -> None:
    if (message.text or "").split(maxsplit=1)[0].lower() == "/start":
        await _finish_dialog(message, message.from_user.id, state)
        return
    await message.answer(
        "Сначала завершите консультацию командой /cancel или кнопкой ниже.",
        reply_markup=ai_dialog_exit_keyboard(),
    )


@router.message(AIState.in_dialog)
async def ai_dialog_non_text(message: Message) -> None:
    await message.answer(
        "ИИ-консультант принимает текстовые вопросы.",
        reply_markup=ai_dialog_exit_keyboard(),
    )
