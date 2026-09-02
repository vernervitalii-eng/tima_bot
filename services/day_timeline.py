from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from statistics import mean
from zoneinfo import ZoneInfo

from database.models import SleepLog, SleepType
from services.time_utils import format_duration, to_local, utc_now


RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
RU_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)


@dataclass(slots=True)
class TimelineEvent:
    at: datetime
    priority: int
    text: str


def local_date_bounds_utc(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(day, time.min, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _date_title(day: date) -> str:
    return f"{day.day} {RU_MONTHS[day.month - 1]}, {RU_WEEKDAYS[day.weekday()]}"


def _sleep_count_label(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = "сон"
    elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        word = "сна"
    else:
        word = "снов"
    return f"{count} {word}"


def build_day_timeline(
    child_name: str,
    selected_date: date,
    timezone_name: str,
    logs: list[SleepLog],
    wake_target_minutes: int,
    now: datetime | None = None,
) -> str:
    _ = wake_target_minutes  # Параметр сохранён для совместимости публичного API.
    now = now or utc_now()
    start_utc, end_utc = local_date_bounds_utc(selected_date, timezone_name)
    today = to_local(now, timezone_name).date()
    effective_end = min(end_utc, now) if selected_date == today else end_utc
    events: list[TimelineEvent] = []
    total_sleep = timedelta()
    day_sleep = timedelta()
    day_count = 0

    ordered_logs = sorted(logs, key=lambda item: item.start_time)
    for log in ordered_logs:
        raw_end = log.end_time or (now if selected_date == today else end_utc)
        clipped_start = max(log.start_time, start_utc)
        clipped_end = min(raw_end, effective_end)
        if clipped_end > clipped_start:
            clipped_duration = clipped_end - clipped_start
            total_sleep += clipped_duration
            if log.sleep_type == SleepType.DAY.value:
                day_sleep += clipped_duration
                day_count += 1

        local_start = to_local(log.start_time, timezone_name)
        local_end = to_local(log.end_time, timezone_name) if log.end_time else None
        full_duration = (log.end_time - log.start_time) if log.end_time else (now - log.start_time)

        if log.sleep_type == SleepType.NIGHT.value:
            if local_end and local_end.date() == selected_date:
                events.append(TimelineEvent(
                    local_end,
                    20,
                    f"<code>{local_end:%H:%M}</code> · Подъём "
                    f"<i>(ночь: <code>{format_duration(full_duration)}</code>)</i>",
                ))
            if local_start.date() == selected_date:
                events.append(TimelineEvent(
                    local_start,
                    20,
                    f"<code>{local_start:%H:%M}</code> · Укладывание на ночь",
                ))
            continue

        if local_start.date() <= selected_date and (not local_end or local_end.date() >= selected_date):
            visible_start = max(local_start, datetime.combine(selected_date, time.min, tzinfo=local_start.tzinfo))
            end_label = f"{local_end:%H:%M}" if local_end else "сейчас"
            number = sum(
                1
                for candidate in ordered_logs
                if candidate.sleep_type == SleepType.DAY.value
                and to_local(candidate.start_time, timezone_name).date() == selected_date
                and candidate.start_time <= log.start_time
            )
            number = max(number, 1)
            events.append(TimelineEvent(
                visible_start,
                20,
                f"<code>{visible_start:%H:%M} – {end_label}</code> · Дневной сон {number} "
                f"<i>(<code>{format_duration(full_duration)}</code>)</i>",
            ))

    wake_intervals: list[timedelta] = []
    for previous, following in zip(ordered_logs, ordered_logs[1:]):
        if not previous.end_time or following.start_time <= previous.end_time:
            continue
        local_end = to_local(previous.end_time, timezone_name)
        local_next = to_local(following.start_time, timezone_name)
        if local_end.date() != selected_date or local_next.date() != selected_date:
            continue
        duration = following.start_time - previous.end_time
        if duration > timedelta(hours=12):
            continue
        wake_intervals.append(duration)

    active = next((item for item in reversed(ordered_logs) if item.end_time is None), None)
    last_completed = next((item for item in reversed(ordered_logs) if item.end_time is not None), None)
    if selected_date == today and active is None and last_completed and last_completed.end_time:
        local_last_end = to_local(last_completed.end_time, timezone_name)
        if local_last_end.date() == selected_date and now > last_completed.end_time:
            current_wake = now - last_completed.end_time
            events.append(TimelineEvent(
                to_local(now, timezone_name),
                90,
                f"Сейчас · Бодрствует <code>{format_duration(current_wake)}</code>",
            ))

    events.sort(key=lambda item: (item.at, item.priority))
    visible_events = events[:30]
    blocks = [event.text for event in visible_events]
    if len(events) > len(visible_events):
        blocks.append(f"… <i>Ещё событий: {len(events) - len(visible_events)}</i>")
    if not blocks:
        blocks.append("<i>За этот день пока нет записей.</i>")

    average_wake = (
        timedelta(seconds=mean(item.total_seconds() for item in wake_intervals))
        if wake_intervals else None
    )
    summary = (
        "<b>Итог за день</b>\n"
        f"• Дневной сон: <code>{format_duration(day_sleep)}</code> ({_sleep_count_label(day_count)})\n"
        f"• Среднее бодрствование: <code>{format_duration(average_wake)}</code>\n"
        f"• Всего сна: <code>{format_duration(total_sleep)}</code>"
    )
    return (
        f"<b>{_date_title(selected_date)}</b>\n"
        f"<i>{escape(child_name)}</i>\n\n"
        + "\n".join(blocks)
        + "\n\n"
        + summary
    )
