from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from statistics import mean
from zoneinfo import ZoneInfo

from database.models import ActivityLog, SleepLog, SleepType
from services.time_utils import format_duration, to_local, utc_now


RU_MONTHS = (
    "ЯНВАРЯ", "ФЕВРАЛЯ", "МАРТА", "АПРЕЛЯ", "МАЯ", "ИЮНЯ",
    "ИЮЛЯ", "АВГУСТА", "СЕНТЯБРЯ", "ОКТЯБРЯ", "НОЯБРЯ", "ДЕКАБРЯ",
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
    return f"{day.day} {RU_MONTHS[day.month - 1]}"


def _progress_bar(duration: timedelta, target_minutes: int) -> str:
    minutes = max(int(duration.total_seconds() // 60), 0)
    filled = min(10, max(0, round(minutes / max(target_minutes, 1) * 8)))
    return "▓" * filled + "░" * (10 - filled)


def _activity_event(activity: ActivityLog, timezone_name: str) -> TimelineEvent:
    local = to_local(activity.timestamp, timezone_name)
    details = escape((activity.details or "").strip())
    if activity.activity_type == "feeding":
        suffix = f" • {details.capitalize()}" if details else ""
        text = f"🍼 <code>{local:%H:%M}</code>  <b>Кормление{suffix}</b>"
    elif activity.activity_type == "diaper":
        text = f"🧷 <code>{local:%H:%M}</code>  <b>Подгузник</b>"
    else:
        note = details[:180] + ("…" if len(details) > 180 else "")
        text = f"📝 <code>{local:%H:%M}</code>  <b>Заметка</b>"
        if note:
            text += f"\n└ <i>{note}</i>"
    return TimelineEvent(local, 0, text)


def build_day_timeline(
    child_name: str,
    selected_date: date,
    timezone_name: str,
    logs: list[SleepLog],
    activities: list[ActivityLog],
    wake_target_minutes: int,
    now: datetime | None = None,
) -> str:
    now = now or utc_now()
    start_utc, end_utc = local_date_bounds_utc(selected_date, timezone_name)
    today = to_local(now, timezone_name).date()
    effective_end = min(end_utc, now) if selected_date == today else end_utc
    events: list[TimelineEvent] = [_activity_event(item, timezone_name) for item in activities]
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
                    f"🌅 <code>{local_end:%H:%M}</code>  <b>Подъём</b>\n"
                    f"└ 🌙 <i>Ночной сон: {format_duration(full_duration)}</i>",
                ))
            if local_start.date() == selected_date:
                events.append(TimelineEvent(
                    local_start,
                    20,
                    f"🌙 <code>{local_start:%H:%M}</code>  <b>Укладывание в ночь</b>",
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
                f"💤 <code>{visible_start:%H:%M} — {end_label}</code>  <b>Дневной сон №{number}</b>\n"
                f"└ ⏳ <i>Длительность: {format_duration(full_duration)}</i>",
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
        events.append(TimelineEvent(
            local_next,
            10,
            f"⏱ <code>ВБ: {format_duration(duration)}</code> "
            f"<code>{_progress_bar(duration, wake_target_minutes)}</code>",
        ))

    active = next((item for item in reversed(ordered_logs) if item.end_time is None), None)
    last_completed = next((item for item in reversed(ordered_logs) if item.end_time is not None), None)
    if selected_date == today and active is None and last_completed and last_completed.end_time:
        local_last_end = to_local(last_completed.end_time, timezone_name)
        if local_last_end.date() == selected_date and now > last_completed.end_time:
            current_wake = now - last_completed.end_time
            events.append(TimelineEvent(
                to_local(now, timezone_name),
                90,
                f"⚡ <code>Текущее ВБ: {format_duration(current_wake)}</code> "
                f"<code>{_progress_bar(current_wake, wake_target_minutes)}</code>",
            ))

    events.sort(key=lambda item: (item.at, item.priority))
    visible_events = events[:30]
    blocks = [event.text for event in visible_events]
    if len(events) > len(visible_events):
        blocks.append(f"… <i>Ещё событий: {len(events) - len(visible_events)}</i>")
    if not blocks:
        blocks.append("🫧 <i>За этот день пока нет записей.</i>")

    average_wake = (
        timedelta(seconds=mean(item.total_seconds() for item in wake_intervals))
        if wake_intervals else None
    )
    summary = (
        "────────────────────\n"
        "📊 <b>СВОДКА ЗА СУТКИ</b>\n"
        f"• 💤 Дневной сон: <code>{format_duration(day_sleep)}</code> ({day_count} снов)\n"
        f"• ⏱ Среднее ВБ: <code>{format_duration(average_wake)}</code>\n"
        f"• 🔋 Общий сон: <code>{format_duration(total_sleep)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👶 <b>РЕЖИМ ДНЯ • {_date_title(selected_date)}</b>\n"
        f"<code>{escape(child_name)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + summary
    )
