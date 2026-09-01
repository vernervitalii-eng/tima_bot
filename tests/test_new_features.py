import ast
from datetime import date, datetime, timedelta
from pathlib import Path

from database.models import SleepLog, SleepType
from chart_generator import generate_sleep_chart
from keyboards.inline import ai_refresh_keyboard, day_date_keyboard, day_period_keyboard, history_keyboard
from keyboards.main import main_keyboard
from services.ai_analyst import RoutineAnalysis, ScheduleItem, build_sleep_history, format_analysis_card
from services.day_timeline import build_day_timeline, local_date_bounds_utc
from services.sleep_insights import build_wake_widget, typical_wake_minutes
from services.live_status import build_live_status_card


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
    card = build_day_timeline(
        "Тима <3",
        selected,
        "UTC",
        logs,
        wake_target_minutes=180,
        now=datetime(2025, 3, 21, 12),
    )
    assert "ХРОНОЛОГИЯ • 20 МАРТА" in card
    assert "Тима &lt;3" in card
    assert "Подъём" in card
    assert "ВБ: 2 ч 30 мин" in card
    assert "Дневной сон: <code>2 ч 00 мин</code> (2 снов)" in card
    assert "Всего сна за сутки: <code>14 ч 00 мин</code>" in card


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
    assert labels[0] == "💤 Уснул"
    assert "☀️ Проснулся" not in labels
    assert "📅 Хронология дня" in labels
    assert "🧠 AI-Режим" in labels
    assert "📊 График снов" in labels
    sleeping_labels = [button.text for row in main_keyboard(True).keyboard for button in row]
    assert sleeping_labels[0] == "☀️ Проснулся"
    assert "💤 Уснул" not in sleeping_labels
    assert history_keyboard([1], 0, 1).inline_keyboard[0][0].callback_data == "history:delete:1:0"


def test_live_status_card_for_sleep_and_wake():
    active = sleep(datetime(2025, 3, 20, 10), datetime(2025, 3, 20, 11))
    active.end_time = None
    sleeping = build_live_status_card(
        "Тима",
        "UTC",
        datetime(2025, 3, 20, 11, 15),
        active,
        None,
        180,
    )
    assert "СТАТУС: РЕБЁНОК СПИТ" in sleeping
    assert "10:00" in sleeping
    assert "1 ч 15 мин" in sleeping

    completed = sleep(datetime(2025, 3, 20, 10), datetime(2025, 3, 20, 11))
    awake = build_live_status_card(
        "Тима",
        "UTC",
        datetime(2025, 3, 20, 12, 30),
        None,
        completed,
        180,
    )
    assert "СТАТУС: РЕБЁНОК БОДРСТВУЕТ" in awake
    assert "Проснулся в: <code>11:00</code>" in awake
    assert "~14:00" in awake


def test_ai_card_is_html_safe_and_fits_telegram_limit():
    analysis = RoutineAnalysis(
        insights=["Стабильное окно <10:30>", "Позднее укладывание"],
        schedule=[
            ScheduleItem(time="07:15", event="Подъём"),
            ScheduleItem(time="10:30–11:45", event="Первый сон"),
            ScheduleItem(time="20:15", event="Ночной сон"),
        ],
        tips=["Начинать ритуал заранее", "Следить за признаками усталости"],
        caveat="Данных пока немного",
    )
    card = format_analysis_card(analysis, 14)
    assert "&lt;10:30&gt;" in card
    assert "На основе 14 дней" in card
    assert len(card) < 4096


def test_chart_generator_returns_png_buffer():
    logs = [sleep(datetime(2025, 3, 19, 20), datetime(2025, 3, 20, 7), SleepType.NIGHT.value)]
    image = generate_sleep_chart(
        logs,
        "UTC",
        date(2025, 3, 18),
        date(2025, 3, 20),
        norm_hours=13,
        child_name="Тест",
    )
    assert image.getvalue().startswith(b"\x89PNG\r\n\x1a\n")


def test_all_callback_handlers_answer_immediately():
    for path in Path("handlers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            is_callback = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "callback_query"
                for decorator in node.decorator_list
            )
            if not is_callback:
                continue
            first = node.body[0]
            assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Await), (
                path, node.name
            )
            call = first.value.value
            assert (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "answer"
            ), (path, node.name)
