"""Совместимая точка входа; основной код приложения остаётся в bot.py."""

from bot import main


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

