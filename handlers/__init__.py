from aiogram import Dispatcher

from handlers import activities, common, exports, family, sleep, statistics, status


def register_handlers(dp: Dispatcher) -> None:
    # Порядок важен: специализированный текстовый парсер идёт перед fallback.
    # Команды /join и /reset должны иметь приоритет над незавершённым онбордингом.
    dp.include_router(family.router)
    dp.include_router(common.router)
    dp.include_router(sleep.router)
    dp.include_router(activities.router)
    dp.include_router(status.router)
    dp.include_router(exports.router)
    dp.include_router(statistics.router)
