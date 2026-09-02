from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from statistics import median

from database.models import SleepLog
from services.time_utils import format_duration, to_local


def typical_wake_minutes(logs: list[SleepLog], fallback_minutes: int) -> tuple[int, int]:
    completed = sorted((item for item in logs if item.end_time), key=lambda item: item.start_time)
    samples: list[int] = []
    for previous, following in zip(completed, completed[1:]):
        gap = following.start_time - previous.end_time
        minutes = int(gap.total_seconds() // 60)
        if 20 <= minutes <= 8 * 60:
            samples.append(minutes)
    if len(samples) < 3:
        return fallback_minutes, len(samples)
    return int(median(samples[-14:])), len(samples)


def build_wake_widget(
    child_name: str,
    wake_at: datetime,
    sleep_duration: timedelta,
    typical_minutes: int,
    timezone_name: str,
    history_samples: int,
    author: str,
    previous_wake: timedelta | None = None,
) -> str:
    target = wake_at + timedelta(minutes=typical_minutes)
    window_start = target - timedelta(minutes=10)
    window_end = target + timedelta(minutes=5)
    ritual_at = target - timedelta(minutes=25)
    local_wake = to_local(wake_at, timezone_name)
    local_start = to_local(window_start, timezone_name)
    local_end = to_local(window_end, timezone_name)
    local_ritual = to_local(ritual_at, timezone_name)
    source = "по истории сна" if history_samples >= 3 else "по возрастному ориентиру"
    until_window = max(window_start - wake_at, timedelta())
    previous_line = (
        f"Предыдущее бодрствование: <code>{format_duration(previous_wake)}</code>\n"
        if previous_wake is not None else ""
    )
    return (
        f"<b>Подъём зафиксирован в <code>{local_wake:%H:%M}</code></b>\n"
        f"<i>{escape(child_name)}</i>\n\n"
        f"Сон длился: <code>{format_duration(sleep_duration)}</code>\n"
        f"{previous_line}"
        f"Обычное ВБ: <code>~{format_duration(timedelta(minutes=typical_minutes))}</code> "
        f"<i>{source}</i>\n\n"
        f"Окно следующего сна: <code>~{local_start:%H:%M} – {local_end:%H:%M}</code>\n"
        f"<i>Примерно через {format_duration(until_window)}</i>\n"
        f"Ритуалы: <code>{local_ritual:%H:%M}</code>\n\n"
        f"<i>Добавил(а): {escape(author)}</i>"
    )
