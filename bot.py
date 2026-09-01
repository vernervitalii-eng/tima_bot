"""Совместимая точка запуска для существующей команды Render: python bot.py."""

import asyncio

from main import main


if __name__ == "__main__":
    asyncio.run(main())
