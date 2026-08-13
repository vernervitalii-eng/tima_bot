from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import load_settings
from database.session import close_db, init_db
from handlers import register_handlers
from services.scheduler import restore_jobs, scheduler


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await init_db(settings.database_url)
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings
    register_handlers(dp)

    scheduler.start()
    await restore_jobs(bot)
    try:
        # Удаляем webhook, чтобы polling мог стартовать после любого способа деплоя.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

