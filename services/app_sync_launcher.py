"""Optional bridge launcher using the existing bot and tracking handlers."""
import asyncio
import logging


async def run_bridges(settings, bot) -> None:
    from services.app_sync import sync_loop
    # Independent network loops: a problem in one family cannot stop the other.
    tasks = [asyncio.create_task(sync_loop(item, bot), name=f'app-sleep-sync-{index}')
             for index, item in enumerate(settings)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def launch() -> None:
    from main import main
    from services.app_sync import BridgeSettings
    async def start_sync(bot):
        try:
            settings = BridgeSettings.from_env()
            if settings:
                sources = [settings]
                try:
                    sources.extend(BridgeSettings.extra_from_env(settings))
                except Exception as error:
                    logging.getLogger(__name__).warning('Additional app sync disabled (%s); existing family continues', type(error).__name__)
                return asyncio.create_task(run_bridges(sources, bot), name="app-sleep-sync")
        except Exception as error:
            logging.getLogger(__name__).warning("App sync disabled (%s); original bot starts normally", type(error).__name__)
        return None
    # SQLite, the existing Bot instance and reminders must be ready before writes.
    await main(background_factory=start_sync)


if __name__ == "__main__":
    asyncio.run(launch())
