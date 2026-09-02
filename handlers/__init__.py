from aiogram import Dispatcher

from handlers import admin_tools, ai_dialog, ai_routine, chart, common, day, exports, family, history, parser as parser_handler, sleep, statistics, status, subscription


def register_handlers(dp: Dispatcher) -> None:
    # Порядок важен: специализированный текстовый парсер идёт перед fallback.
    # Команды /join и /reset должны иметь приоритет над незавершённым онбордингом.
    dp.include_router(family.router)
    # Платежи и /subscribe должны обрабатываться даже во время AI-диалога.
    dp.include_router(subscription.router)
    # FSM-диалог должен перехватывать /cancel и обычный текст раньше трекеров.
    dp.include_router(ai_dialog.router)
    dp.include_router(common.router)
    # FSM редактирования истории должен получить время раньше живого трекера сна.
    dp.include_router(history.router)
    dp.include_router(sleep.router)
    dp.include_router(status.router)
    dp.include_router(exports.router)
    dp.include_router(day.router)
    dp.include_router(ai_routine.router)
    dp.include_router(chart.router)
    dp.include_router(admin_tools.router)
    dp.include_router(parser_handler.router)
    dp.include_router(statistics.router)
