"""Optional launcher. Existing bot entrypoint/tracking code remains unchanged."""
import asyncio
import logging
from contextlib import suppress


async def launch() -> None:
    from main import main
    from services.app_sync import BridgeSettings, sync_loop
    task = None
    try:
        settings = BridgeSettings.from_env()
        if settings:
            task = asyncio.create_task(sync_loop(settings), name="app-sleep-sync")
    except Exception as error:
        logging.getLogger(__name__).warning("App sync disabled (%s); original bot starts normally", type(error).__name__)
    try:
        await main()
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    asyncio.run(launch())
