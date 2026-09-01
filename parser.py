"""Устойчивый разбор коротких сообщений только о сне и бодрствовании.

Парсер намеренно не пишет в базу: он возвращает нормализованные события, а
сохранение выполняется отдельным слоем CRUD с дедупликацией. Это позволяет
использовать один и тот же код для ручных сообщений и пакетного seed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal


EventKind = Literal["sleep_start", "wake"]

_TIME_RE = re.compile(r"(?<!\d)(?P<hour>\d{1,2})[:.](?P<minute>\d{2})(?!\d)")
_DATE_RE = re.compile(r"(?<!\d)(?P<day>\d{1,2})[./](?P<month>\d{1,2})(?:[./](?P<year>\d{2,4}))?(?!\d)")
_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}[:.]\d{2})\s*[–—-]\s*(?P<end>\d{1,2}[:.]\d{2})"
)
_SLEEP_START_WORDS = r"уснул(?:а|и)?|заснул(?:а|и)?|усыпил(?:а|и)?|уложил(?:а|и)?"
_WAKE_WORDS = r"проснулся|проснулась|проснулись|встал(?:а|и)?|поднялся|поднялась"
_POINT_TIME_FIRST_RE = re.compile(
    rf"(?P<time>\d{{1,2}}[:.]\d{{2}})\s*(?P<kind>{_SLEEP_START_WORDS}|{_WAKE_WORDS})",
    re.IGNORECASE,
)
_POINT_WORD_FIRST_RE = re.compile(
    rf"(?P<kind>{_SLEEP_START_WORDS}|{_WAKE_WORDS})\s*(?:в\s*)?(?P<time>\d{{1,2}}[:.]\d{{2}})",
    re.IGNORECASE,
)
@dataclass(frozen=True, slots=True)
class ParsedSleep:
    start: datetime
    end: datetime | None
    sleep_type: Literal["day", "night"]
    duration_minutes: int | None
    wake_before_minutes: int | None = None
    source_line: str = ""


@dataclass(slots=True)
class ParseResult:
    sleeps: list[ParsedSleep] = field(default_factory=list)
    point_events: list[tuple[EventKind, datetime, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean_line(line: str) -> str:
    line = re.sub(r"\[[^\]]+\]\s*[^:]{0,40}:\s*", "", line)
    return line.replace("*", "").replace("_", " ").strip()


def _parse_date_token(match: re.Match[str], reference_date: date) -> date:
    year_raw = match.group("year")
    year = int(year_raw) if year_raw else reference_date.year
    if year < 100:
        year += 2000
    try:
        return date(year, int(match.group("month")), int(match.group("day")))
    except ValueError as exc:
        raise ValueError(f"Некорректная дата: {match.group(0)}") from exc


def _parse_clock(raw: str) -> time:
    hour, minute = re.split(r"[:.]", raw)
    value = time(int(hour), int(minute))
    return value


def _at(base: date, raw: str, previous: datetime | None = None) -> datetime:
    result = datetime.combine(base, _parse_clock(raw))
    if previous is not None and result <= previous:
        # Переход через полночь в фразе «уснул в 21:00, проснулся в 07:30».
        result += timedelta(days=1)
    return result


def _event_kind(raw: str) -> EventKind:
    return "wake" if re.fullmatch(_WAKE_WORDS, raw, re.IGNORECASE) else "sleep_start"


def _sleep_type(start: datetime, duration_minutes: int | None) -> Literal["day", "night"]:
    if start.hour >= 19 or start.hour < 6 or (duration_minutes is not None and duration_minutes >= 300):
        return "night"
    return "day"


def parse_text(text: str, reference_date: date | None = None) -> ParseResult:
    """Разбирает текст пользователя и связывает пары «уснул → проснулся».

    Дата без года трактуется как дата текущего года относительно
    ``reference_date``. Если дата не указана, используется дата сообщения.
    """
    reference_date = reference_date or date.today()
    current_date = reference_date
    point_events: list[tuple[EventKind, datetime, str]] = []
    ranges: list[tuple[datetime, datetime, str]] = []
    warnings: list[str] = []

    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        lower = line.lower()
        line_date = current_date
        keyword_date = re.search(r"\b(позавчера|вчера|сегодня)\b", lower)
        if keyword_date:
            offsets = {"позавчера": -2, "вчера": -1, "сегодня": 0}
            line_date = reference_date + timedelta(days=offsets[keyword_date.group(1)])
        date_match = _DATE_RE.search(line)
        if date_match:
            try:
                line_date = _parse_date_token(date_match, reference_date)
                current_date = line_date
            except ValueError:
                warnings.append(f"Пропущена некорректная дата: {date_match.group(0)}")
        range_match = _RANGE_RE.search(line)
        # Заголовок даты без событий только меняет контекст следующих строк.
        if not _TIME_RE.search(line) and not _RANGE_RE.search(line):
            continue

        if range_match and re.search(r"\b(спал|спала|сон|спит)\b", lower):
            start = _at(line_date, range_match.group("start"))
            end = _at(line_date, range_match.group("end"), start)
            if end <= start:
                end += timedelta(days=1)
            ranges.append((start, end, line))

        # Оба порядка слов поддерживаются: «07:00 проснулся» и «уснул в 21:00».
        matches: list[tuple[int, EventKind, str]] = []
        for match in _POINT_TIME_FIRST_RE.finditer(line):
            # В конструкции «уснул в 21:00» это время уже относится к
            # verb-first regex, а не к следующему слову после времени.
            before_time = line[max(0, match.start() - 3):match.start()]
            if re.search(r"\bв\s*$", before_time, re.IGNORECASE):
                continue
            matches.append((match.start(), _event_kind(match.group("kind")), match.group("time")))
        for match in _POINT_WORD_FIRST_RE.finditer(line):
            matches.append((match.start(), _event_kind(match.group("kind")), match.group("time")))
        matches.sort(key=lambda item: item[0])
        previous: datetime | None = None
        for _, kind, clock in matches:
            event_at = _at(line_date, clock, previous)
            point_events.append((kind, event_at, line))
            previous = event_at

    # Связываем точечные события. Повторный «уснул» до пробуждения обычно
    # означает исправление времени; сохраняем последнее значение и сообщаем об этом.
    pending_start: datetime | None = None
    pending_source = ""
    linked: list[tuple[datetime, datetime, str]] = list(ranges)
    for kind, event_at, source in sorted(point_events, key=lambda item: item[1]):
        if kind == "sleep_start":
            if pending_start is not None:
                warnings.append(f"Повторное засыпание без пробуждения около {pending_start:%d.%m %H:%M}; использовано последнее время.")
            pending_start, pending_source = event_at, source
            continue
        if pending_start is None:
            warnings.append(f"Пробуждение {event_at:%d.%m %H:%M} без парного засыпания.")
            continue
        if event_at <= pending_start:
            warnings.append(f"Пробуждение раньше засыпания: {event_at:%d.%m %H:%M}.")
            continue
        linked.append((pending_start, event_at, pending_source))
        pending_start = None
        pending_source = ""

    sleeps: list[ParsedSleep] = []
    for start, end, source in sorted(linked, key=lambda item: item[0]):
        duration = max(int((end - start).total_seconds() // 60), 0)
        sleeps.append(ParsedSleep(start, end, _sleep_type(start, duration), duration, source_line=source))
    if pending_start is not None:
        duration = None
        sleeps.append(ParsedSleep(pending_start, None, _sleep_type(pending_start, duration), duration, source_line=pending_source))

    # Интервал бодрствования считаем между окончанием предыдущего сна и началом следующего.
    previous_end: datetime | None = None
    for index, sleep in enumerate(sleeps):
        wake_before = None
        if previous_end is not None and sleep.start > previous_end:
            wake_before = int((sleep.start - previous_end).total_seconds() // 60)
        sleeps[index] = ParsedSleep(
            sleep.start, sleep.end, sleep.sleep_type, sleep.duration_minutes,
            wake_before, sleep.source_line,
        )
        if sleep.end is not None:
            previous_end = sleep.end

    # Удаляем повторные события, которые могли быть распознаны обоими regex.
    unique_points: list[tuple[EventKind, datetime, str]] = []
    seen: set[tuple[EventKind, datetime]] = set()
    for event in sorted(point_events, key=lambda item: item[1]):
        key = (event[0], event[1])
        if key not in seen:
            unique_points.append(event)
            seen.add(key)
    return ParseResult(sleeps=sleeps, point_events=unique_points, warnings=warnings)


def format_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    hours, rest = divmod(max(minutes, 0), 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    if hours:
        return f"{hours} ч"
    return f"{rest} мин"


parse_message = parse_text
