"""Optional bridge launcher using the existing bot and tracking handlers."""
import asyncio
import logging


async def launch() -> None:
    from main import main
    from services.app_sync import BridgeSettings, sync_loop
    async def start_sync(bot):
        try:
            settings = BridgeSettings.from_env()
            if settings:
                return asyncio.create_task(sync_loop(settings, bot), name="app-sleep-sync")
        except Exception as error:
            logging.getLogger(__name__).warning("App sync disabled (%s); original bot starts normally", type(error).__name__)
        return None
    # SQLite, the existing Bot instance and reminders must be ready before writes.
    await main(background_factory=start_sync)


if __name__ == "__main__":
    asyncio.run(launch())
