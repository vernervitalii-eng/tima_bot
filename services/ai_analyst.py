from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from html import escape
from statistics import mean

from pydantic import BaseModel, Field

from database.models import SleepLog
from services.time_utils import to_local, utc_now


_BASE_ROUTINE_CACHE: dict[int, str] = {}


class ScheduleItem(BaseModel):
    time: str = Field(description="Время или диапазон времени в формате ЧЧ:ММ")
    event: str = Field(description="Короткое название события сна или бодрствования")


class RoutineAnalysis(BaseModel):
    insights: list[str] = Field(min_length=2, max_length=5)
    schedule: list[ScheduleItem] = Field(min_length=3, max_length=8)
    tips: list[str] = Field(min_length=2, max_length=5)
    caveat: str = Field(description="Короткое предупреждение об ограниченности данных")


def _is_retryable_gemini_error(exc: Exception) -> bool:
    """Распознаёт временные ошибки Gemini, для которых безопасен повтор запроса."""
    raw_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = None
    return code in {429, 500, 502, 503, 504} or str(exc).lstrip().startswith(
        ("429", "500", "502", "503", "504")
    )


async def _generate_with_fallback(
    api_key: str,
    model_name: str,
    contents,
    config: dict[str, object],
):
    """Единая политика повторов для анализа режима и диалога."""
    from google import genai

    candidate_models = list(dict.fromkeys((model_name, "gemini-3.6-flash")))
    last_error: Exception | None = None
    for model_index, candidate_model in enumerate(candidate_models):
        attempts = 2 if model_index == 0 else 1
        for attempt in range(attempts):
            try:
                async with genai.Client(api_key=api_key).aio as client:
                    response = await client.models.generate_content(
                        model=candidate_model,
                        contents=contents,
                        config=config,
                    )
                if not response.text:
                    raise RuntimeError("Gemini вернул пустой ответ")
                return response
            except Exception as exc:
                if not _is_retryable_gemini_error(exc):
                    raise
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.5 * (attempt + 1))

    raise RuntimeError("Gemini временно перегружен после повторных попыток") from last_error


def build_sleep_history(logs: list[SleepLog], timezone_name: str) -> list[dict[str, object]]:
    completed = sorted((item for item in logs if item.end_time), key=lambda item: item.start_time)
    history: list[dict[str, object]] = []
    previous_end: datetime | None = None
    for log in completed:
        local_start = to_local(log.start_time, timezone_name)
        local_end = to_local(log.end_time, timezone_name)
        wake_before = None
        if previous_end and log.start_time > previous_end:
            candidate = int((log.start_time - previous_end).total_seconds() // 60)
            # Большой разрыв обычно означает неполные записи, а не реальное ВБ.
            if 20 <= candidate <= 12 * 60:
                wake_before = candidate
        history.append({
            "date": local_start.date().isoformat(),
            "sleep_start": local_start.strftime("%H:%M"),
            "sleep_end": local_end.strftime("%H:%M"),
            "duration_minutes": int((log.end_time - log.start_time).total_seconds() // 60),
            "wake_before_minutes": wake_before,
            "sleep_type": log.sleep_type,
        })
        previous_end = log.end_time
    return history


def serialize_base_routine(analysis: RoutineAnalysis) -> str:
    return json.dumps(
        analysis.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def remember_base_routine(child_id: int, analysis: RoutineAnalysis) -> str:
    """Кэширует режим и возвращает JSON для постоянного безопасного снимка."""
    payload = serialize_base_routine(analysis)
    _BASE_ROUTINE_CACHE[child_id] = payload
    return payload


def get_last_base_routine(child_id: int) -> str | None:
    return _BASE_ROUTINE_CACHE.get(child_id)


def trim_dialog_history(
    history: list[dict[str, str]] | None,
    max_exchanges: int = 6,
) -> list[dict[str, str]]:
    """Оставляет не более шести последних пар пользователь/консультант."""
    cleaned: list[dict[str, str]] = []
    for item in history or []:
        role = str(item.get("role", "")).strip().lower()
        text = str(item.get("text", "")).strip()
        if role not in {"user", "assistant"} or not text:
            continue
        cleaned.append({"role": role, "text": text[:3500]})
    return cleaned[-max(max_exchanges, 1) * 2:]


def build_consultation_context(
    logs: list[SleepLog],
    timezone_name: str,
    age_months: int,
    dialog_history: list[dict[str, str]] | None,
    base_routine: str | None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Строит обезличенный фактический контекст для консультации Gemini."""
    current = now or utc_now()
    history = build_sleep_history(logs, timezone_name)
    local_today = to_local(current, timezone_name).date()
    cutoff = (local_today - timedelta(days=13)).isoformat()
    recent = [item for item in history if str(item["date"]) >= cutoff]
    wake_samples = [
        int(item["wake_before_minutes"])
        for item in history
        if item["wake_before_minutes"] is not None
    ]
    day_durations = [
        int(item["duration_minutes"])
        for item in history
        if item["sleep_type"] == "day"
    ]
    night_durations = [
        int(item["duration_minutes"])
        for item in history
        if item["sleep_type"] == "night"
    ]
    active = next((item for item in reversed(logs) if item.end_time is None), None)
    if active is not None:
        current_status: dict[str, object] = {
            "state": "sleeping",
            "since": to_local(active.start_time, timezone_name).strftime("%Y-%m-%d %H:%M"),
            "elapsed_minutes": max(int((current - active.start_time).total_seconds() // 60), 0),
        }
    elif history:
        current_status = {
            "state": "awake",
            "since": f"{history[-1]['date']} {history[-1]['sleep_end']}",
        }
    else:
        current_status = {"state": "unknown"}

    def average(values: list[int]) -> int | None:
        return round(mean(values)) if values else None

    return {
        "child_age_months": age_months,
        "timezone": timezone_name,
        "current_status": current_status,
        "month_summary": {
            "observed_days": len({item["date"] for item in history}),
            "completed_sleeps": len(history),
            "average_wake_minutes": average(wake_samples),
            "average_day_sleep_minutes": average(day_durations),
            "average_night_sleep_minutes": average(night_durations),
        },
        "sleep_history_month": history,
        "sleep_history_last_14_days": recent,
        "last_base_routine": base_routine or "Базовый AI-режим ещё не создавался; рассчитай его по истории.",
        "dialog_history": trim_dialog_history(dialog_history),
    }


async def ask_sleep_consultant(
    api_key: str,
    model_name: str,
    age_months: int,
    timezone_name: str,
    logs: list[SleepLog],
    question: str,
    dialog_history: list[dict[str, str]] | None = None,
    base_routine: str | None = None,
) -> str:
    """Отвечает на вопрос родителя с опорой на историю и текущий диалог."""
    context = build_consultation_context(
        logs,
        timezone_name,
        age_months,
        dialog_history,
        base_routine,
    )
    system_instruction = (
        "Ты профессиональный консультант по детскому сну. Отвечай по-русски, спокойно и конкретно. "
        "Опирайся только на цифры и события из переданного контекста и явно отделяй факты от предположений. "
        "Не ставь медицинских диагнозов и при тревожных симптомах советуй обратиться к педиатру. "
        "Учитывай пожелания родителя как новые ограничения. Если пользователь просит сдвинуть сон, перейти "
        "на другое число снов или сообщает новое обстоятельство, пересчитай весь оставшийся день и выдай блок "
        "«ОБНОВЛЁННЫЙ ПОЧАСОВОЙ ГРАФИК» с точными временными слотами. Объясни 2–4 ключевые причины и "
        "предложи практический следующий шаг. Не используй Markdown или HTML — только аккуратный обычный текст."
    )
    prompt = (
        "КОНТЕКСТ РЕЖИМА:\n"
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "НОВЫЙ ВОПРОС ИЛИ ЗАМЕЧАНИЕ РОДИТЕЛЯ:\n"
        f"{question.strip()[:2000]}"
    )
    response = await _generate_with_fallback(
        api_key,
        model_name,
        prompt,
        {
            "system_instruction": system_instruction,
            "max_output_tokens": 1600,
            "temperature": 0.35,
        },
    )
    return response.text.strip()


def format_consultant_answer(answer: str) -> str:
    escaped_parts: list[str] = []
    escaped_length = 0
    for character in answer.strip():
        escaped_character = escape(character)
        if escaped_length + len(escaped_character) > 3400:
            escaped_parts.append("…")
            break
        escaped_parts.append(escaped_character)
        escaped_length += len(escaped_character)
    safe = "".join(escaped_parts)
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>ОТВЕТ ИИ-КОНСУЛЬТАНТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{safe}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Рекомендации справочные и не заменяют консультацию педиатра.</i>"
    )


async def analyze_routine(
    api_key: str,
    model_name: str,
    age_months: int,
    timezone_name: str,
    logs: list[SleepLog],
) -> tuple[RoutineAnalysis, int]:
    history = build_sleep_history(logs, timezone_name)
    observed_days = len({item["date"] for item in history})
    payload = {
        "age_months": age_months,
        "observed_days": observed_days,
        "sleep_history": history,
    }
    prompt = (
        "Ты аналитик детского сна. Проанализируй только переданные наблюдения за месяц: найди устойчивые "
        "временные окна сна, различия окон бодрствования до обеда и перед ночью, признаки "
        "систематического недосыпа, перегула или слишком короткого бодрствования. Учитывай ночные "
        "подъёмы и регулярность переходов между сном и бодрствованием. Определи окна первого/второго сна и ночного "
        "укладывания. Составь реалистичный стабильный почасовой график. Не выдумывай корреляции, "
        "которых недостаточно в данных, не ставь диагнозов "
        "и не заменяй рекомендации педиатра. Дай 3–4 точечных совета. Пиши по-русски, кратко и конкретно.\n\n"
        f"Данные наблюдений:\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    request_config = {
        "max_output_tokens": 1400,
        "response_mime_type": "application/json",
        "response_json_schema": RoutineAnalysis.model_json_schema(),
    }
    response = await _generate_with_fallback(api_key, model_name, prompt, request_config)
    return RoutineAnalysis.model_validate_json(response.text), observed_days


def format_analysis_card(analysis: RoutineAnalysis, observed_days: int) -> str:
    insights = "\n".join(f"• {escape(item[:280])}" for item in analysis.insights[:4])
    schedule_lines = []
    visible_schedule = analysis.schedule[:7]
    for index, item in enumerate(visible_schedule):
        branch = "└" if index == len(visible_schedule) - 1 else "├"
        schedule_lines.append(f"{branch} <code>{escape(item.time[:40])}</code> — {escape(item.event[:140])}")
    tips = "\n".join(
        f"{index}. {escape(item[:280])}" for index, item in enumerate(analysis.tips[:4], start=1)
    )
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>AI-АНАЛИЗ И РЕКОМЕНДОВАННЫЙ РЕЖИМ</b>\n"
        f"<i>На основе {observed_days} дней наблюдений</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>Ключевые инсайты</b>\n{insights}\n\n"
        "🕒 <b>Рекомендуемый почасовой распорядок</b>\n"
        + "\n".join(schedule_lines)
        + "\n\n💡 <b>Персональные советы</b>\n"
        + tips
        + "\n\n────────────────────\n"
        f"<i>{escape(analysis.caveat[:300])}</i>\n"
        "<i>AI-анализ носит справочный характер и не заменяет консультацию педиатра.</i>"
    )
