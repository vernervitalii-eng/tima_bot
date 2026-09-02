from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from config import Settings
from database import crud
from database.session import db_session, read_sqlite_bytes
from parser import parse_text
from services.time_utils import local_to_utc, to_local, utc_now


router = Router(name="admin_tools")
SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "august_2026.txt"


def _utc_seed_events(parsed, timezone_name: str):
    return [
        replace(
            item,
            start=local_to_utc(item.start, timezone_name),
            end=local_to_utc(item.end, timezone_name) if item.end else None,
        )
        for item in parsed.sleeps
    ]


@router.message(Command("seed_data"))
async def seed_data(message: Message, command: CommandObject) -> None:
    """Импортирует августовскую историю только для текущего профиля администратора."""
    requested_code = (command.args or "").strip().upper()
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        if user.role != "admin":
            await message.answer("Пакетный импорт доступен только администратору семьи.")
            return
        if requested_code and requested_code != user.child.invite_code.upper():
            await message.answer("Код семьи не совпадает с вашим профилем. Проверьте код и повторите команду.")
            return
        if not SEED_FILE.exists():
            await message.answer("Файл истории августа не найден в поставке бота.")
            return
        parsed = parse_text(
            SEED_FILE.read_text(encoding="utf-8"),
            # Источник явно помечен как август 2026; фиксируем год, чтобы
            # повторный импорт после Нового года не создал записи в будущем.
            reference_date=date(2026, 8, 31),
        )
        sleeps = _utc_seed_events(parsed, user.child.timezone)
        stats = await crud.seed_monthly_data(session, user.child_id, user.id, sleeps)
        child_name = user.child.name

    warning_text = ""
    if parsed.warnings:
        warning_text = "\n\n⚠️ Неоднозначные строки не потеряны, но требуют проверки:\n" + "\n".join(
            f"• {warning[:180]}" for warning in parsed.warnings[:5]
        )
    await message.answer(
        f"<b>История августа загружена · {child_name}</b>\n\n"
        f"Добавлено снов: <code>{stats['sleep_added']}</code>\n"
        f"Уже были в базе: <code>{stats['sleep_skipped']}</code>\n"
        f"Повторный запуск безопасен и не создаёт дубликаты.{warning_text}"
    )


@router.message(Command("backup"))
async def backup(message: Message, settings: Settings) -> None:
    """Отправляет актуальный SQLite-файл администратору в личный чат."""
    async with db_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not user:
            await message.answer("Сначала выполните /start.")
            return
        if user.role != "admin":
            await message.answer("Резервную копию может запросить только администратор семьи.")
            return
        child_name = user.child.name

    if not settings.db_path or not settings.database_url.startswith("sqlite"):
        await message.answer("Для текущего PostgreSQL-хранилища файловый backup недоступен.")
        return
    try:
        payload = await read_sqlite_bytes(settings.db_path)
    except FileNotFoundError:
        await message.answer("Файл базы данных пока не найден.")
        return
    filename = f"sleep_tracker_backup_{to_local(utc_now(), settings.timezone):%Y-%m-%d_%H-%M}.db"
    try:
        await message.bot.send_document(
            chat_id=message.from_user.id,
            document=BufferedInputFile(payload, filename=filename),
            caption=f"🛡 Резервная копия профиля «{child_name}». Записей не изменялось.",
        )
    except TelegramAPIError:
        await message.answer("Не удалось отправить backup в личные сообщения. Откройте бота через /start.")
    else:
        if message.chat.id != message.from_user.id:
            await message.answer("🛡 Backup отправлен вам в личные сообщения.")
