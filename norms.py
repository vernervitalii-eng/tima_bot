from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SleepNorm:
    wake_min: int
    wake_max: int
    sleep_min: float
    sleep_max: float


# Последний диапазон продлён до 24 месяцев, как требует продукт. Для 19–24 мес
# используются близкие нормы 13–18 мес, поскольку отдельные значения не заданы ТЗ.
NORMS = (
    (0, 1, SleepNorm(45, 60, 16, 18)),
    (2, 3, SleepNorm(60, 90, 15, 17)),
    (4, 5, SleepNorm(90, 135, 14, 15)),
    (6, 8, SleepNorm(135, 180, 13, 14)),
    (9, 12, SleepNorm(180, 240, 12, 14)),
    (13, 24, SleepNorm(240, 300, 12, 13)),
)


def norm_for_age(months: int) -> SleepNorm:
    for start, end, norm in NORMS:
        if start <= months <= end:
            return norm
    return NORMS[0][2] if months < 0 else NORMS[-1][2]


def wake_recommendation(current_minutes: int, norm: SleepNorm) -> str:
    if current_minutes < norm.wake_min:
        return "До обычного окна сна ещё есть время. Ориентируйтесь также на признаки усталости."
    if current_minutes <= norm.wake_max - 15:
        return "Окно сна приближается — можно постепенно снижать активность."
    if current_minutes <= norm.wake_max:
        return "Скоро закроется окно сна — пора начинать ритуал укладывания."
    return "Время бодрствования выше ориентира. Если ребёнок устал, попробуйте уложить его сейчас."

