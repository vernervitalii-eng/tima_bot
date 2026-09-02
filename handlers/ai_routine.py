from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import Settings
from database import crud
from database.session import db_session
from keyboards.inline import ai_refresh_keyboard
from services.ai_analyst import (
    analyze_routine,
    build_sleep_history,
    format_analysis_card,
    remember_base_routine,
)
from services.time_utils import age_parts, to_local, utc_now
from services.subscription import require_premium_access


logger = logging.getLogger(__name__)
router = Router(name="ai_routine")


async def _run_analysis(message: Message, telegram_id: int, settings: Settings) -> None:
    if not await require_premium_access(message, telegram_id):
        return
    if not settings.gemini_api_key:
        await message.answer(
            "🧠 <b>AI-анализ пока не настроен</b>\n\n"
            "Добавьте <code>GEMINI_API_KEY</code> в переменные окружения и перезапустите бота."
        )
        return

    since = utc_now() - timedelta(days=31)
    async with db_session() as session:
        user = await crud.get_user(session, telegram_id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        logs = await crud.completed_sleeps_since(session, user.child_id, since)
        birth_date = user.child.birth_date
        timezone_name = user.child.timezone
        child_id = user.child_id
        today = to_local(utc_now(), timezone_name).date()
        age_months, _ = age_parts(birth_date, today)

    history = build_sleep_history(logs, timezone_name)
    observed_days = len({item["date"] for item in history})
    if len(history) < 5 or observed_days < 3:
        await message.answer(
            "🫧 <b>Пока мало данных для AI-анализа</b>\n\n"
            "Нужно минимум <code>3 дня</code> наблюдений и <code>5 завершённых снов</code>. "
            "Продолжайте отмечать сон и пробуждения — команда станет доступна автоматически."
        )
        return

    progress = await message.answer(
        "🧠 Анализирую режим и ищу устойчивые окна сна…"
    )
    try:
        analysis, days = await analyze_routine(
            settings.gemini_api_key,
            settings.gemini_model,
            age_months,
            timezone_name,
            logs,
        )
        base_routine = remember_base_routine(child_id, analysis)
        async with db_session() as session:
            await crud.save_ai_routine_snapshot(session, child_id, base_routine, utc_now())
        card = format_analysis_card(analysis, days)
    except Exception:
        logger.exception("Не удалось выполнить Gemini-анализ режима")
        await progress.edit_text(
            "⚠️ <b>AI-анализ временно недоступен</b>\n\n"
            "Проверьте <code>GEMINI_API_KEY</code>, модель <code>GEMINI_MODEL</code> и попробуйте позже."
        )
        return
    await progress.edit_text(card, reply_markup=ai_refresh_keyboard())


@router.message(Command("ai_routine"))
@router.message(F.text == "🧠 AI-анализ")
@router.message(F.text == "🧠 AI-Режим")
@router.message(F.text == "🧠 AI-Режим (Gemini)")
@router.message(F.text == "🧠 Режим (AI)")
async def ai_routine(message: Message, settings: Settings) -> None:
    await _run_analysis(message, message.from_user.id, settings)


@router.callback_query(F.data == "ai:refresh")
async def ai_refresh(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer("Обновляю анализ…")
    await _run_analysis(callback.message, callback.from_user.id, settings)
