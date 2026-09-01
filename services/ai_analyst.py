from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html import escape

from pydantic import BaseModel, Field

from database.models import SleepLog
from services.time_utils import to_local


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


async def analyze_routine(
    api_key: str,
    model_name: str,
    age_months: int,
    timezone_name: str,
    logs: list[SleepLog],
) -> tuple[RoutineAnalysis, int]:
    from google import genai

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
    # SDK уже повторяет часть временных ошибок. Дополнительно делаем один
    # прикладной повтор и, если основной endpoint перегружен, переключаемся на
    # предыдущую стабильную Flash-модель с тем же форматом ответа.
    candidate_models = list(dict.fromkeys((model_name, "gemini-3.6-flash")))
    last_error: Exception | None = None
    for model_index, candidate_model in enumerate(candidate_models):
        attempts = 2 if model_index == 0 else 1
        for attempt in range(attempts):
            try:
                async with genai.Client(api_key=api_key).aio as client:
                    response = await client.models.generate_content(
                        model=candidate_model,
                        contents=prompt,
                        config=request_config,
                    )
                if not response.text:
                    raise RuntimeError("Gemini вернул пустой ответ")
                return RoutineAnalysis.model_validate_json(response.text), observed_days
            except Exception as exc:
                if not _is_retryable_gemini_error(exc):
                    raise
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.5 * (attempt + 1))

    raise RuntimeError("Gemini временно перегружен после повторных попыток") from last_error


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
