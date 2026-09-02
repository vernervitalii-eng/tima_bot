import ast
import asyncio
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text

from config import ADMIN_IDS, BUILTIN_ADMIN_IDS, parse_admin_ids
from database import crud
from database.models import SubscriptionPayment, User
from database.session import close_db, db_session, init_db
from keyboards.inline import premium_tariffs_keyboard
from keyboards.main import main_keyboard
from services.subscription import (
    PREMIUM_PLANS,
    has_premium_access,
    is_admin_user,
    make_invoice_payload,
    parse_invoice_payload,
    premium_deadline,
    premium_status_text,
    premium_storefront_text,
)
from services.time_utils import utc_now


def test_safe_migration_adds_trial_without_losing_legacy_user(tmp_path):
    database_path = tmp_path / "legacy-subscription.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE children ("
            "id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL, birth_date DATE NOT NULL, "
            "created_at DATETIME, invite_code VARCHAR(12) NOT NULL, timezone VARCHAR(64) NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL, child_id INTEGER NOT NULL, "
            "role VARCHAR(16) NOT NULL, display_name VARCHAR(80) NOT NULL)"
        )
        connection.execute(
            "INSERT INTO children VALUES (1, 'Тима', '2025-01-01', "
            "'2026-01-01 00:00:00', 'LEGACY01', 'UTC')"
        )
        connection.execute(
            "INSERT INTO users VALUES (1, 770001, 1, 'admin', 'Мама')"
        )

    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        before = utc_now()
        await init_db(url)
        try:
            async with db_session() as session:
                columns = {
                    row[1]
                    for row in (
                        await session.execute(text("PRAGMA table_info(users)"))
                    ).all()
                }
                user = await crud.get_user(session, 770001)
                assert {"trial_end_date", "subscription_end_date"} <= columns
                assert user is not None and user.display_name == "Мама"
                assert user.trial_end_date is not None
                assert before + timedelta(days=2, hours=23) < user.trial_end_date
                saved_trial = user.trial_end_date
            await close_db()

            await init_db(url)
            async with db_session() as session:
                user = await crud.get_user(session, 770001)
                assert user is not None
                assert user.trial_end_date == saved_trial
                assert await session.scalar(select(func.count()).select_from(User)) == 1
        finally:
            await close_db()

    asyncio.run(scenario())


def test_trial_payment_extension_and_charge_idempotency(tmp_path):
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{(tmp_path / 'premium.db').as_posix()}"
        await init_db(url)
        try:
            now = datetime(2026, 9, 1, 12, 0)
            async with db_session() as session:
                user = await crud.create_family(
                    session,
                    770002,
                    "Тест Premium",
                    date(2025, 1, 1),
                    "UTC",
                )
                assert user.trial_end_date is not None
                assert has_premium_access(user, utc_now())
                user_id = user.id

            plan = PREMIUM_PLANS[0]
            payload = make_invoice_payload(plan, 770002)
            async with db_session() as session:
                user = await session.get(User, user_id)
                first_end, activated = await crud.activate_subscription(
                    session,
                    user,
                    plan_code=plan.code,
                    days=plan.days,
                    stars=plan.stars,
                    currency="XTR",
                    telegram_payment_charge_id="charge-001",
                    invoice_payload=payload,
                    paid_at=now,
                )
                assert activated is True
                assert first_end == now + timedelta(days=30)

            async with db_session() as session:
                user = await session.get(User, user_id)
                duplicate_end, activated = await crud.activate_subscription(
                    session,
                    user,
                    plan_code=plan.code,
                    days=plan.days,
                    stars=plan.stars,
                    currency="XTR",
                    telegram_payment_charge_id="charge-001",
                    invoice_payload=payload,
                    paid_at=now,
                )
                assert activated is False
                assert duplicate_end == first_end
                assert await session.scalar(
                    select(func.count()).select_from(SubscriptionPayment)
                ) == 1

            second = PREMIUM_PLANS[1]
            async with db_session() as session:
                user = await session.get(User, user_id)
                second_end, activated = await crud.activate_subscription(
                    session,
                    user,
                    plan_code=second.code,
                    days=second.days,
                    stars=second.stars,
                    currency="XTR",
                    telegram_payment_charge_id="charge-002",
                    invoice_payload=make_invoice_payload(second, 770002),
                    paid_at=now + timedelta(minutes=1),
                )
                assert activated is True
                assert second_end == first_end + timedelta(days=90)
                assert premium_deadline(user, now) == second_end
        finally:
            await close_db()

    asyncio.run(scenario())


def test_creators_always_have_lifetime_premium():
    assert BUILTIN_ADMIN_IDS == frozenset({303225689, 324310407})
    assert BUILTIN_ADMIN_IDS <= ADMIN_IDS
    for telegram_id in BUILTIN_ADMIN_IDS:
        expired_user = User(
            telegram_id=telegram_id,
            child_id=1,
            trial_end_date=datetime(2020, 1, 1),
            subscription_end_date=None,
        )
        assert is_admin_user(telegram_id)
        assert has_premium_access(telegram_id)
        assert has_premium_access(expired_user, datetime(2030, 1, 1))
        assert "Создатель бота" in premium_status_text(expired_user)
        assert "Пожизненный" in premium_status_text(expired_user)
        assert "Создатель бота" in premium_storefront_text(telegram_id=telegram_id)
    assert parse_admin_ids(" 111,invalid,222,,0 ") == frozenset({111, 222})


def test_manual_premium_grant_extends_existing_access(tmp_path):
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{(tmp_path / 'manual-premium.db').as_posix()}"
        await init_db(url)
        try:
            now = datetime(2026, 9, 2, 12, 0)
            async with db_session() as session:
                user = await crud.create_family(
                    session,
                    770004,
                    "Ручная выдача",
                    date(2025, 1, 1),
                    "UTC",
                )
                user.subscription_end_date = now + timedelta(days=10)
                expected_user_id = user.id

            async with db_session() as session:
                user = await session.get(User, expected_user_id)
                granted_until = await crud.grant_premium(
                    session,
                    user,
                    days=5,
                    granted_at=now,
                )
                assert granted_until == now + timedelta(days=15)
        finally:
            await close_db()

    asyncio.run(scenario())


def test_plans_payloads_keyboards_and_premium_menu_are_consistent():
    assert [(plan.days, plan.stars) for plan in PREMIUM_PLANS] == [
        (30, 500),
        (90, 1250),
        (180, 2200),
    ]
    callbacks = [
        button.callback_data
        for row in premium_tariffs_keyboard(PREMIUM_PLANS).inline_keyboard
        for button in row
    ]
    assert callbacks == ["premium:buy:1m", "premium:buy:3m", "premium:buy:6m"]
    for plan in PREMIUM_PLANS:
        payload = make_invoice_payload(plan, 770003)
        assert parse_invoice_payload(payload) == (plan, 770003)
    assert parse_invoice_payload("premium:unknown:770003") is None
    assert parse_invoice_payload("broken") is None

    labels = [button.text for row in main_keyboard(False).keyboard for button in row]
    assert labels.count("⭐️ Premium подписка") == 1
    onboarding_labels = [
        button.text for row in main_keyboard(None).keyboard for button in row
    ]
    assert onboarding_labels[:2] == ["💤 Уснул", "☀️ Проснулся"]


def test_payment_and_premium_handlers_are_registered_safely():
    source = Path("handlers/subscription.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    pre_checkout = functions["premium_pre_checkout"]
    first = pre_checkout.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Await)
    assert "query.answer" in ast.unparse(first)
    assert "ok=True" in ast.unparse(first)

    router_source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert router_source.index("subscription.router") < router_source.index("ai_dialog.router")
    assert "F.successful_payment" in source
    assert 'currency="XTR"' in source
    assert "telegram_payment_charge_id" in source
    assert 'Command("give_premium")' in source
    assert "settings.admin_ids" in source


def test_premium_features_use_access_gate():
    for path in (
        "handlers/ai_routine.py",
        "handlers/ai_dialog.py",
        "handlers/chart.py",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "require_premium_access" in source
