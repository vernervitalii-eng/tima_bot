from __future__ import annotations

import re
import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    # SQLite хранит naive UTC; преобразование в локальное время делается на границе UI.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(value: datetime, timezone_name: str) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name))


def local_to_utc(value: datetime, timezone_name: str) -> datetime:
    return value.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc).replace(tzinfo=None)


def parse_anchored_local_time(
    text: str,
    anchor_date: date,
    timezone_name: str,
) -> datetime | None:
    """Разбирает ЧЧ:ММ на дате записи либо полное ДД.ММ.ГГГГ ЧЧ:ММ."""
    match = re.fullmatch(
        r"\s*(?:(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s+)?"
        r"(\d{1,2})[:.](\d{2})\s*",
        text,
    )
    if not match:
        return None
    day_raw, month_raw, year_raw, hour_raw, minute_raw = match.groups()
    year = anchor_date.year if year_raw is None else int(year_raw)
    if year < 100:
        year += 2000
    try:
        selected_date = (
            anchor_date
            if day_raw is None
            else date(year, int(month_raw), int(day_raw))
        )
        local_value = datetime.combine(
            selected_date,
            time(int(hour_raw), int(minute_raw)),
        )
    except ValueError:
        return None
    return local_to_utc(local_value, timezone_name)


def local_day_start_utc(timezone_name: str, now: datetime | None = None) -> datetime:
    current = to_local(now or utc_now(), timezone_name)
    local_start = datetime.combine(current.date(), time.min)
    return local_to_utc(local_start, timezone_name)


def parse_birth_date(text: str) -> date:
    value = datetime.strptime(text.strip(), "%d.%m.%Y").date()
    if value > date.today():
        raise ValueError("Дата рождения не может быть в будущем")
    if value < date.today() - timedelta(days=365 * 10):
        raise ValueError("Проверьте год рождения")
    return value


def age_parts(birth: date, today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    months = (today.year - birth.year) * 12 + today.month - birth.month
    if today.day < birth.day:
        months -= 1
        prev_month = today.month - 1 or 12
        prev_year = today.year if today.month > 1 else today.year - 1
        anchor_day = min(birth.day, calendar.monthrange(prev_year, prev_month)[1])
        anchor = date(prev_year, prev_month, anchor_day)
    else:
        anchor = date(today.year, today.month, min(birth.day, calendar.monthrange(today.year, today.month)[1]))
    return max(months, 0), max((today - anchor).days, 0)


def parse_relative_time(text: str, timezone_name: str, now: datetime | None = None) -> datetime | None:
    """Понимает `уснул в 14:15` и `проснулся 20 минут назад`."""
    now_utc = now or utc_now()
    lower = text.lower().replace("ё", "е")
    ago = re.search(r"(\d{1,3})\s*(минут(?:у|ы)?|мин)\s*назад", lower)
    if ago:
        return now_utc - timedelta(minutes=int(ago.group(1)))
    ago_hours = re.search(r"(\d{1,2})\s*(?:час(?:а|ов)?|ч)\s*назад", lower)
    if ago_hours:
        return now_utc - timedelta(hours=int(ago_hours.group(1)))
    clock = re.search(r"(?:\bв\s*)?(\d{1,2})[:.]([0-5]\d)\b", lower)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour > 23:
            return None
        local_now = to_local(now_utc, timezone_name)
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > local_now + timedelta(minutes=2):
            candidate -= timedelta(days=1)
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)
    return None


def format_duration(delta: timedelta | None) -> str:
    if delta is None or delta.total_seconds() < 0:
        return "нет данных"
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин" if hours else f"{minutes} мин"


def is_quiet_hours(timezone_name: str, now: datetime | None = None) -> bool:
    hour = to_local(now or utc_now(), timezone_name).hour
    return hour >= 22 or hour < 7
