from datetime import date, datetime, timedelta

from database.models import ActivityLog, SleepLog, SleepType
from keyboards.inline import ai_refresh_keyboard, day_date_keyboard, day_period_keyboard
from keyboards.main import main_keyboard
from services.ai_analyst import RoutineAnalysis, ScheduleItem, build_sleep_history, format_analysis_card
from services.day_timeline import build_day_timeline, local_date_bounds_utc
from services.sleep_insights import build_wake_widget, typical_wake_minutes


def sleep(start: datetime, end: datetime, sleep_type: str = SleepType.DAY.value) -> SleepLog:
    return SleepLog(
        child_id=1,
        created_by_user_id=1,
        start_time=start,
        end_time=end,
        sleep_type=sleep_type,
    )


def test_day_timeline_calculates_sleep_and_wake_intervals():
    selected = date(2025, 3, 20)
    logs = [
        sleep(datetime(2025, 3, 19, 20), datetime(2025, 3, 20, 6), SleepType.NIGHT.value),
        sleep(datetime(2025, 3, 20, 8, 30), datetime(2025, 3, 20, 9, 30)),
        sleep(datetime(2025, 3, 20, 13), datetime(2025, 3, 20, 14)),
        sleep(datetime(2025, 3, 20, 18), datetime(2025, 3, 21, 6), SleepType.NIGHT.value),
    ]
    activities = [
        ActivityLog(
            child_id=1,
            activity_type="feeding",
            timestamp=datetime(2025, 3, 20, 10),
            details="грудь",
            created_by_user_id=1,
        )
    ]
    card = build_day_timeline(
        "Тима <3",
        selected,
        "UTC",
        logs,
        activities,
        wake_target_minutes=180,
        now=datetime(2025, 3, 21, 12),
    )
    assert "РЕЖИМ ДНЯ • 20 МАРТА" in card
    assert "Тима &lt;3" in card
    assert "Подъём" in card
    assert "ВБ: 2 ч 30 мин" in card
    assert "Кормление • Грудь" in card
    assert "Дневной сон: <code>2 ч 00 мин</code> (2 снов)" in card
    assert "Общий сон: <code>14 ч 00 мин</code>" in card


def test_local_date_bounds_are_timezone_aware():
    start, end = local_date_bounds_utc(date(2025, 3, 20), "Europe/Chisinau")
    assert start == datetime(2025, 3, 19, 22)
    assert end == datetime(2025, 3, 20, 22)


def test_typical_wake_and_live_widget():
    logs = [
        sleep(datetime(2025, 3, 20, 6), datetime(2025, 3, 20, 7)),
        sleep(datetime(2025, 3, 20, 10), datetime(2025, 3, 20, 11)),
        sleep(datetime(2025, 3, 20, 14, 10), datetime(2025, 3, 20, 15)),
        sleep(datetime(2025, 3, 20, 18, 20), datetime(2025, 3, 20, 19)),
    ]
    minutes, samples = typical_wake_minutes(logs, 120)
    assert (minutes, samples) == (190, 3)
    card = build_wake_widget(
        "Тима",
        datetime(2025, 3, 20, 11),
        timedelta(hours=1),
        minutes,
        "UTC",
        samples,
        "Мама",
    )
    assert "ПОДЪЁМ ЗАФИКСИРОВАН • 11:00" in card
    assert "13:60" not in card
    assert "14:00 — 14:15" in card
    assert "спокойные ритуалы в <code>13:45</code>" in card


def test_ai_history_and_new_keyboards():
    logs = [sleep(datetime(2025, 3, 20, 10), datetime(2025, 3, 20, 11))]
    history = build_sleep_history(logs, "UTC")
    assert history[0]["duration_minutes"] == 60
    assert history[0]["sleep_start"] == "10:00"

    period_callbacks = [
        button.callback_data for row in day_period_keyboard().inline_keyboard for button in row
    ]
    assert period_callbacks == ["day:today", "day:yesterday", "day:pick"]
    date_callbacks = [
        button.callback_data for row in day_date_keyboard(date(2025, 3, 20), days=2).inline_keyboard for button in row
    ]
    assert date_callbacks[:2] == ["day:date:2025-03-20", "day:date:2025-03-19"]
    assert ai_refresh_keyboard().inline_keyboard[0][0].callback_data == "ai:refresh"

    labels = [button.text for row in main_keyboard(False).keyboard for button in row]
    assert "📅 Хронология дня" in labels
    assert "🧠 AI-анализ" in labels


def test_ai_card_is_html_safe_and_fits_telegram_limit():
    analysis = RoutineAnalysis(
        insights=["Стабильное окно <10:30>", "Позднее укладывание"],
        schedule=[
            ScheduleItem(time="07:15", activity="Подъём"),
            ScheduleItem(time="10:30–11:45", activity="Первый сон"),
            ScheduleItem(time="20:15", activity="Ночной сон"),
        ],
        tips=["Начинать ритуал заранее", "Следить за признаками усталости"],
        caveat="Данных пока немного",
    )
    card = format_analysis_card(analysis, 14)
    assert "&lt;10:30&gt;" in card
    assert "На основе 14 дней" in card
    assert len(card) < 4096
