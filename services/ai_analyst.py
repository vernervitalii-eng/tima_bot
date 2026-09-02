from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from html import escape
from statistics import mean

from pydantic import BaseModel, Field, ValidationError

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


class ConsultantAnswer(BaseModel):
    answer: str = Field(description="Прямой краткий ответ родителю с опорой на цифры")
    key_facts: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Факты из истории, которые объясняют рекомендацию",
    )
    updated_schedule: list[ScheduleItem] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Полный обновлённый график из 3–8 точных временных слотов, если родитель "
            "просит изменить время, число снов или перестроить день"
        ),
    )
    actions: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Практические следующие шаги для родителя",
    )
    caveat: str = Field(description="Краткое ограничение рекомендации")


class ConsultantScheduleAnswer(ConsultantAnswer):
    updated_schedule: list[ScheduleItem] = Field(
        min_length=3,
        max_length=8,
        description="Обязательный полный пересчитанный график из 3–8 точных временных слотов",
    )


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


async def _generate_validated_response(
    api_key: str,
    model_name: str,
    contents: str,
    config: dict[str, object],
    response_model: type[BaseModel],
) -> BaseModel:
    """Генерирует JSON и один раз безопасно повторяет обрезанный/неполный ответ."""
    response = await _generate_with_fallback(
        api_key,
        model_name,
        contents,
        config,
    )
    try:
        return response_model.model_validate_json(response.text)
    except ValidationError:
        # Gemini иногда завершает структурированный ответ по MAX_TOKENS. Такой
        # ответ нельзя чинить дописыванием скобок: запрашиваем новый полный JSON.
        retry_config = dict(config)
        current_limit = int(retry_config.get("max_output_tokens", 1400))
        retry_config["max_output_tokens"] = min(max(current_limit * 2, 2800), 4096)
        retry_config["temperature"] = min(float(retry_config.get("temperature", 0.3)), 0.2)
        retry_contents = (
            f"{contents}\n\n"
            "КРИТИЧЕСКОЕ ТРЕБОВАНИЕ: предыдущий структурированный ответ был "
            "неполным или не прошёл схему. Сгенерируй заново ОДИН полный валидный JSON "
            "строго по заданной схеме. Не добавляй пояснений вне JSON. Пиши компактно: "
            "каждое текстовое поле — не более 300 символов, но сохрани все обязательные поля."
        )
        response = await _generate_with_fallback(
            api_key,
            model_name,
            retry_contents,
            retry_config,
        )
        return response_model.model_validate_json(response.text)


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


def consultation_requires_schedule(question: str) -> bool:
    normalized = question.lower().replace("ё", "е")
    markers = (
        "давай",
        "смест",
        "пересч",
        "перестро",
        "не подходит",
        "неудоб",
        "один сон",
        "два сна",
        "переходим",
        "поменя",
        "скоррект",
    )
    return any(marker in normalized for marker in markers)


def consultant_answer_to_text(answer: ConsultantAnswer) -> str:
    parts = [answer.answer.strip()]
    if answer.key_facts:
        facts = ["Факты из истории"]
        facts.extend(f"• {item.strip()}" for item in answer.key_facts if item.strip())
        parts.append("\n".join(facts))
    if answer.updated_schedule:
        schedule = ["Обновлённый график"]
        schedule.extend(
            f"• {item.time.strip()} · {item.event.strip()}"
            for item in answer.updated_schedule
        )
        parts.append("\n".join(schedule))
    if answer.actions:
        actions = ["Следующие шаги"]
        actions.extend(
            f"• {item.strip()}"
            for item in answer.actions
            if item.strip()
        )
        parts.append("\n".join(actions))
    if answer.caveat.strip():
        parts.append(f"Важно: {answer.caveat.strip()}")
    return "\n\n".join(part for part in parts if part)


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
        "«Обновлённый график» с точными временными слотами. Объясни 2–4 ключевые причины и "
        "предложи практический следующий шаг. Не используй Markdown или HTML — только аккуратный обычный текст."
    )
    prompt = (
        "КОНТЕКСТ РЕЖИМА:\n"
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "НОВЫЙ ВОПРОС ИЛИ ЗАМЕЧАНИЕ РОДИТЕЛЯ:\n"
        f"{question.strip()[:2000]}"
    )
    requires_schedule = consultation_requires_schedule(question)
    response_model = ConsultantScheduleAnswer if requires_schedule else ConsultantAnswer
    request_config = {
        "system_instruction": system_instruction,
        "max_output_tokens": 2400,
        "temperature": 0.3,
        "response_mime_type": "application/json",
        "response_json_schema": response_model.model_json_schema(),
    }
    parsed = await _generate_validated_response(
        api_key,
        model_name,
        prompt,
        request_config,
        response_model,
    )
    return consultant_answer_to_text(parsed)


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
    safe = re.sub(
        r"(?<!\d)([01]?\d|2[0-3]):[0-5]\d(?!\d)",
        lambda match: f"<code>{match.group(0)}</code>",
        safe,
    )
    safe = re.sub(
        r"(?m)^(Факты из истории|Обновлённый график|Следующие шаги)$",
        r"<b>\1</b>",
        safe,
    )
    return (
        "🧠 <b>Ответ консультанта</b>\n\n"
        f"{safe}\n\n"
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
        "max_output_tokens": 2600,
        "temperature": 0.2,
        "response_mime_type": "application/json",
        "response_json_schema": RoutineAnalysis.model_json_schema(),
    }
    parsed = await _generate_validated_response(
        api_key,
        model_name,
        prompt,
        request_config,
        RoutineAnalysis,
    )
    return parsed, observed_days


def format_analysis_card(analysis: RoutineAnalysis, observed_days: int) -> str:
    insights = "\n".join(f"• {escape(item[:280])}" for item in analysis.insights[:4])
    schedule_lines = []
    visible_schedule = analysis.schedule[:7]
    for item in visible_schedule:
        schedule_lines.append(f"• <code>{escape(item.time[:40])}</code> · {escape(item.event[:140])}")
    tips = "\n".join(
        f"• {escape(item[:280])}" for item in analysis.tips[:4]
    )
    return (
        "🧠 <b>Режим и рекомендации</b>\n"
        f"<i>По данным за {observed_days} дней</i>\n\n"
        f"<b>Наблюдения</b>\n{insights}\n\n"
        "<b>Распорядок</b>\n"
        + "\n".join(schedule_lines)
        + "\n\n<b>Что можно улучшить</b>\n"
        + tips
        + "\n\n"
        f"<i>{escape(analysis.caveat[:300])}</i>\n"
        "<i>AI-анализ носит справочный характер и не заменяет консультацию педиатра.</i>"
    )
