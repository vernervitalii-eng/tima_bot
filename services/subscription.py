from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aiogram.types import Message

from config import ADMIN_IDS
from database import crud
from database.models import User
from database.session import db_session
from keyboards.inline import premium_tariffs_keyboard
from services.time_utils import to_local, utc_now


BRAND_NAME = "BabyRhythm AI"
BRAND_FULL_NAME = "BabyRhythm AI • Умный режим и сон"


@dataclass(frozen=True, slots=True)
class PremiumPlan:
    code: str
    months: int
    days: int
    stars: int
    button_label: str
    invoice_label: str


PREMIUM_PLANS = (
    PremiumPlan(
        code="1m",
        months=1,
        days=30,
        stars=500,
        button_label="⭐️ 1 Месяц — 500 Stars (~10€)",
        invoice_label="Premium на 1 месяц",
    ),
    PremiumPlan(
        code="3m",
        months=3,
        days=90,
        stars=1250,
        button_label="⭐️ 3 Месяца — 1 250 Stars (~24€ | −15%)",
        invoice_label="Premium на 3 месяца",
    ),
    PremiumPlan(
        code="6m",
        months=6,
        days=180,
        stars=2200,
        button_label="⭐️ 6 Месяцев — 2 200 Stars (~42€ | −30%)",
        invoice_label="Premium на 6 месяцев",
    ),
)
PLANS_BY_CODE = {plan.code: plan for plan in PREMIUM_PLANS}


def is_admin_user(user_or_id: User | int) -> bool:
    telegram_id = (
        user_or_id if isinstance(user_or_id, int) else user_or_id.telegram_id
    )
    return telegram_id in ADMIN_IDS


def creator_status_text() -> str:
    return (
        "👑 <b>Статус: Создатель бота</b>\n"
        "<i>Пожизненный безлимитный доступ ко всем функциям</i>"
    )


def premium_deadline(user: User, now: datetime | None = None) -> datetime | None:
    current = now or utc_now()
    active = [
        deadline
        for deadline in (user.trial_end_date, user.subscription_end_date)
        if deadline is not None and deadline > current
    ]
    return max(active) if active else None


def has_premium_access(user: User | int, now: datetime | None = None) -> bool:
    if is_admin_user(user):
        return True
    if isinstance(user, int):
        return False
    return premium_deadline(user, now) is not None


def premium_status_text(
    user: User,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> str:
    if is_admin_user(user):
        return creator_status_text()
    current = now or utc_now()
    deadline = premium_deadline(user, current)
    if deadline is None:
        return "🔒 <b>Premium не активен</b>"
    display_deadline = to_local(deadline, timezone_name) if timezone_name else deadline
    suffix = "" if timezone_name else " UTC"
    if (
        user.subscription_end_date is not None
        and user.subscription_end_date == deadline
        and user.subscription_end_date > current
    ):
        return (
            "⭐️ <b>Premium активен до:</b> "
            f"<code>{display_deadline:%d.%m.%Y %H:%M}{suffix}</code>"
        )
    return (
        "🎁 <b>Пробный Premium до:</b> "
        f"<code>{display_deadline:%d.%m.%Y %H:%M}{suffix}</code>"
    )


def premium_storefront_text(
    user: User | None = None,
    timezone_name: str | None = None,
    telegram_id: int | None = None,
) -> str:
    identity: User | int | None = user if user is not None else telegram_id
    is_creator = identity is not None and is_admin_user(identity)
    if is_creator:
        status = f"\n{creator_status_text()}\n"
    elif user is not None:
        status = f"\n{premium_status_text(user, timezone_name=timezone_name)}\n"
    else:
        status = "\n"
    action = (
        "Тарифы вам не требуются — все Premium-функции уже открыты навсегда."
        if is_creator
        else "Выберите удобный период:"
    )
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐️ <b>PREMIUM ДОСТУП • {BRAND_NAME}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Откройте возможности искусственного интеллекта для режима малыша:\n\n"
        "• 🧠 Неограниченный AI-анализ биоритмов\n"
        "• 💬 Персональный чат-сомнолог 24/7\n"
        "• 📊 Детальные графики снов\n"
        "• 🎯 Точные подсказки окон сна\n"
        f"{status}"
        f"{action}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Цены в евро ориентировочные: стоимость Stars зависит от платформы и региона.</i>\n"
        "Нажимая тариф, вы подтверждаете согласие с /terms. Автопродления нет."
    )


def premium_paywall_text() -> str:
    return (
        "🔒 <b>ЭТО PREMIUM-ФУНКЦИЯ</b>\n\n"
        "Пробный период завершён. Трекинг сна, расчёт длительности, ВБ и "
        "сегодняшняя хронология остаются бесплатными.\n\n"
        + premium_storefront_text()
    )


def make_invoice_payload(plan: PremiumPlan, telegram_id: int) -> str:
    return f"premium:{plan.code}:{telegram_id}"


def parse_invoice_payload(payload: str) -> tuple[PremiumPlan, int] | None:
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "premium" or not parts[2].isdigit():
        return None
    plan = PLANS_BY_CODE.get(parts[1])
    return (plan, int(parts[2])) if plan is not None else None


async def require_premium_access(message: Message, telegram_id: int) -> bool:
    if has_premium_access(telegram_id):
        return True
    async with db_session() as session:
        user = await crud.get_user(session, telegram_id)
        if user is None:
            await message.answer("Сначала выполните /start и создайте профиль ребёнка.")
            return False
        allowed = has_premium_access(user)
    if allowed:
        return True
    await message.answer(
        premium_paywall_text(),
        reply_markup=premium_tariffs_keyboard(PREMIUM_PLANS),
    )
    return False
