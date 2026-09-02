from __future__ import annotations

import logging
from html import escape

from aiogram import F, Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from config import Settings
from database import crud
from database.session import db_session
from handlers.states import AIState
from keyboards.inline import premium_tariffs_keyboard
from keyboards.main import main_keyboard
from services.subscription import (
    BRAND_NAME,
    PLANS_BY_CODE,
    PREMIUM_PLANS,
    make_invoice_payload,
    parse_invoice_payload,
    premium_storefront_text,
)
from services.time_utils import to_local, utc_now


logger = logging.getLogger(__name__)
router = Router(name="subscription")


@router.message(Command("subscribe"))
@router.message(F.text.in_({"⭐️ Premium подписка", "⭐ Premium подписка"}))
async def premium_storefront(message: Message) -> None:
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        timezone_name = user.child.timezone if user is not None else None
    await message.answer(
        premium_storefront_text(user, timezone_name),
        reply_markup=premium_tariffs_keyboard(PREMIUM_PLANS),
    )


@router.callback_query(F.data.regexp(r"^premium:buy:(1m|3m|6m)$"))
async def premium_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    plan = PLANS_BY_CODE.get(callback.data.rsplit(":", 1)[1])
    if plan is None:
        await callback.message.answer("Тариф больше не доступен. Выполните /subscribe.")
        return
    async with db_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
    if user is None:
        await callback.message.answer("Сначала выполните /start и создайте профиль ребёнка.")
        return

    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{BRAND_NAME} Premium • {plan.months} мес.",
        description=(
            f"Доступ к AI-анализу, чату-сомнологу и графикам на {plan.days} дней. "
            "Разовый платёж без автопродления."
        ),
        payload=make_invoice_payload(plan, callback.from_user.id),
        currency="XTR",
        prices=[LabeledPrice(label=plan.invoice_label, amount=plan.stars)],
    )


@router.pre_checkout_query()
async def premium_pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def premium_success(message: Message, state: FSMContext) -> None:
    payment = message.successful_payment
    parsed = parse_invoice_payload(payment.invoice_payload)
    if (
        parsed is None
        or payment.currency != "XTR"
        or parsed[1] != message.from_user.id
        or payment.total_amount != parsed[0].stars
    ):
        logger.error(
            "Отклонена некорректная SuccessfulPayment: currency=%s amount=%s payload=%s",
            payment.currency,
            payment.total_amount,
            payment.invoice_payload,
        )
        await message.answer(
            "⚠️ Платёж получен, но его параметры не прошли проверку. "
            "Напишите /paysupport и сохраните квитанцию Telegram."
        )
        return

    plan, _ = parsed
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if user is None:
            await message.answer(
                "Профиль не найден. Напишите /paysupport и сохраните квитанцию Telegram."
            )
            return
        subscription_end, activated = await crud.activate_subscription(
            session,
            user,
            plan_code=plan.code,
            days=plan.days,
            stars=payment.total_amount,
            currency=payment.currency,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            invoice_payload=payment.invoice_payload,
            paid_at=utc_now(),
        )
        timezone_name = user.child.timezone
        sleeping = await crud.active_sleep(session, user.child_id) is not None

    local_end = to_local(subscription_end, timezone_name)
    if not activated:
        await message.answer(
            "ℹ️ Этот платёж уже обработан — повторного списания периода не произошло.\n"
            f"Premium активен до <code>{local_end:%d.%m.%Y %H:%M}</code>."
        )
        return
    in_ai_dialog = await state.get_state() == AIState.in_dialog.state
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎉 <b>PREMIUM АКТИВИРОВАН!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐️ Тариф: <b>{plan.months} мес.</b>\n"
        f"📅 Доступ до: <code>{local_end:%d.%m.%Y %H:%M}</code>\n\n"
        "Теперь доступны:\n"
        "• 🧠 AI-анализ режима\n"
        "• 💬 Чат с ИИ-сомнологом\n"
        "• 📊 Графики сна\n"
        "• 🎯 Персональные окна сна\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=None if in_ai_dialog else main_keyboard(sleeping),
    )


@router.message(Command("terms"))
async def payment_terms(message: Message) -> None:
    await message.answer(
        "📄 <b>УСЛОВИЯ PREMIUM • BabyRhythm AI</b>\n\n"
        "1. Premium — цифровой доступ к AI-анализу, чату и графикам на выбранный срок.\n"
        "2. Оплата выполняется один раз в Telegram Stars; автоматического продления нет.\n"
        "3. Доступ активируется только после подтверждения successful_payment от Telegram.\n"
        "4. Рекомендации бота информационные и не заменяют врача.\n"
        "5. По вопросам платежа и возврата используйте /paysupport. Telegram Support "
        "не обрабатывает споры о покупках внутри этого бота.\n\n"
        "Нажимая кнопку тарифа, вы подтверждаете согласие с этими условиями."
    )


@router.message(Command("paysupport"))
async def payment_support(
    message: Message,
    command: CommandObject,
    settings: Settings,
    bot: Bot,
) -> None:
    details = (command.args or "").strip()
    if not details:
        await message.answer(
            "🛟 <b>ПОДДЕРЖКА ПО ОПЛАТЕ</b>\n\n"
            "Отправьте команду и кратко опишите проблему:\n"
            "<code>/paysupport оплатил, но Premium не включился</code>\n\n"
            "Сохраните квитанцию Telegram. Telegram Support не решает вопросы покупок "
            "внутри бота — ими занимается владелец BabyRhythm AI."
        )
        return

    notified = 0
    for admin_id in settings.allowed_ids:
        if admin_id == message.from_user.id:
            continue
        try:
            await bot.send_message(
                admin_id,
                "🛟 <b>Запрос по оплате</b>\n"
                f"От пользователя <code>{message.from_user.id}</code>:\n"
                f"{escape(details[:1500])}",
            )
            notified += 1
        except Exception:
            logger.warning("Не удалось отправить запрос поддержки администратору", exc_info=True)
    await message.answer(
        "✅ Запрос по оплате передан владельцу бота."
        if notified
        else "✅ Запрос принят. Сохраните квитанцию Telegram до ответа владельца бота."
    )
