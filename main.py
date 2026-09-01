from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from auth import AllowedIdsMiddleware
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
    backend = "PostgreSQL" if settings.database_url.startswith("postgresql") else "SQLite"
    logging.getLogger(__name__).info("Используется база данных: %s", backend)
    if backend == "SQLite" and os.getenv("RENDER"):
        if settings.db_path.startswith("/var/data/"):
            logging.getLogger(__name__).info("SQLite хранится на постоянном Render Disk: /var/data")
        else:
            logging.getLogger(__name__).warning(
                "SQLite на Render не сохраняется после redeploy/restart. "
                "Подключите Render Disk и задайте DB_PATH=/var/data/baby_tracker.db."
            )

    await init_db(settings.database_url)
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["settings"] = settings
    allowed_ids_middleware = AllowedIdsMiddleware()
    dispatcher.message.middleware(allowed_ids_middleware)
    dispatcher.callback_query.middleware(allowed_ids_middleware)
    register_handlers(dispatcher)

    scheduler.start()
    await restore_jobs(bot)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=True,
        )
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
