"""Рендеринг недельного/двухнедельного отчёта сна в PNG-память."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from services.time_utils import local_to_utc, to_local, utc_now


def _split_by_local_date(logs: Iterable[object], timezone_name: str, dates: list[date]) -> dict[date, dict[str, float]]:
    totals = {day: {"night": 0.0, "day": 0.0} for day in dates}
    date_set = set(dates)
    for log in logs:
        start = getattr(log, "start_time")
        end = getattr(log, "end_time", None) or utc_now()
        if end <= start:
            continue
        kind = "night" if getattr(log, "sleep_type", "day") == "night" else "day"
        cursor = start
        while cursor < end:
            local_cursor = to_local(cursor, timezone_name)
            next_midnight = datetime.combine(local_cursor.date() + timedelta(days=1), time.min)
            segment_end = min(end, local_to_utc(next_midnight, timezone_name))
            if local_cursor.date() in date_set:
                totals[local_cursor.date()][kind] += max((segment_end - cursor).total_seconds(), 0) / 3600
            if segment_end <= cursor:
                break
            cursor = segment_end
    return totals


def generate_sleep_chart(
    logs: Iterable[object],
    timezone_name: str,
    start_date: date,
    end_date: date,
    norm_hours: float | None = None,
    child_name: str = "Ребёнок",
) -> BytesIO:
    """Создаёт PNG со stacked-барами ночного и дневного сна."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Для построения графика установите matplotlib.") from exc

    if end_date < start_date:
        raise ValueError("Дата окончания графика раньше даты начала")
    dates = [start_date + timedelta(days=index) for index in range((end_date - start_date).days + 1)]
    totals = _split_by_local_date(logs, timezone_name, dates)
    night = [totals[day]["night"] for day in dates]
    day_sleep = [totals[day]["day"] for day in dates]
    total = [night[index] + day_sleep[index] for index in range(len(dates))]

    fig, ax = plt.subplots(figsize=(10.5, 5.8), dpi=180)
    fig.patch.set_facecolor("#F7FAFC")
    ax.set_facecolor("#F7FAFC")
    x = list(range(len(dates)))
    ax.bar(x, night, label="Ночной сон", color="#173B67", width=0.68)
    ax.bar(x, day_sleep, bottom=night, label="Дневные сны", color="#43B7C5", width=0.68)
    if norm_hours is not None and norm_hours > 0:
        ax.axhline(norm_hours, color="#E58B39", linestyle=(0, (4, 3)), linewidth=1.6,
                   label=f"Ориентир {norm_hours:g} ч")
    for index, value in enumerate(total):
        if value > 0:
            ax.text(index, value + 0.15, f"{value:.1f} ч", ha="center", va="bottom",
                    fontsize=8.5, color="#1F2937", fontweight="bold")
    ax.set_title(f"Сон {child_name} • {start_date:%d.%m}–{end_date:%d.%m}",
                 fontsize=15, fontweight="bold", color="#173B67", pad=14)
    ax.set_ylabel("Часы сна", color="#4B5563")
    ax.set_xticks(x, [day.strftime("%d.%m") for day in dates])
    ax.grid(axis="y", color="#D7E0EA", linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#AAB8C7")
    ax.legend(frameon=False, ncol=3, loc="upper left", bbox_to_anchor=(0, 1.02))
    ymax = max(max(total, default=0) + 1.2, (norm_hours or 0) + 1.0, 4.0)
    ax.set_ylim(0, ymax)
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer


create_sleep_chart = generate_sleep_chart
