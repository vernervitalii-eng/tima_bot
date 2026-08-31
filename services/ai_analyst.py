from __future__ import annotations

import json
from datetime import datetime
from html import escape

from pydantic import BaseModel, Field

from database.models import SleepLog
from services.time_utils import to_local


class ScheduleItem(BaseModel):
    time: str = Field(description="Время или диапазон времени в формате ЧЧ:ММ")
    activity: str = Field(description="Короткое название события режима")


class RoutineAnalysis(BaseModel):
    insights: list[str] = Field(min_length=2, max_length=5)
    schedule: list[ScheduleItem] = Field(min_length=3, max_length=8)
    tips: list[str] = Field(min_length=2, max_length=5)
    caveat: str = Field(description="Короткое предупреждение об ограниченности данных")


def build_sleep_history(logs: list[SleepLog], timezone_name: str) -> list[dict[str, object]]:
    completed = sorted((item for item in logs if item.end_time), key=lambda item: item.start_time)
    history: list[dict[str, object]] = []
    previous_end: datetime | None = None
    for log in completed:
        local_start = to_local(log.start_time, timezone_name)
        local_end = to_local(log.end_time, timezone_name)
        wake_before = None
        if previous_end and log.start_time > previous_end:
            wake_before = int((log.start_time - previous_end).total_seconds() // 60)
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
        "Ты аналитик детского сна. Проанализируй только переданные наблюдения: найди устойчивые "
        "временные окна сна, различия окон бодрствования до обеда и перед ночью, признаки "
        "систематического недосыпа или слишком длинного бодрствования. Составь реалистичный "
        "почасовой режим. Не выдумывай корреляции, которых недостаточно в данных, не ставь диагнозов "
        "и не заменяй рекомендации педиатра. Пиши по-русски, кратко и конкретно.\n\n"
        f"Данные наблюдений:\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    async with genai.Client(api_key=api_key).aio as client:
        response = await client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={
                "temperature": 0.2,
                "max_output_tokens": 1400,
                "response_mime_type": "application/json",
                "response_json_schema": RoutineAnalysis.model_json_schema(),
            },
        )
    if not response.text:
        raise RuntimeError("Gemini вернул пустой ответ")
    return RoutineAnalysis.model_validate_json(response.text), observed_days


def format_analysis_card(analysis: RoutineAnalysis, observed_days: int) -> str:
    insights = "\n".join(f"• {escape(item[:280])}" for item in analysis.insights[:4])
    schedule_lines = []
    visible_schedule = analysis.schedule[:7]
    for index, item in enumerate(visible_schedule):
        branch = "└" if index == len(visible_schedule) - 1 else "├"
        schedule_lines.append(f"{branch} <code>{escape(item.time[:40])}</code> — {escape(item.activity[:140])}")
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
