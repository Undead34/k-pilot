"""
K-Pilot: KDE Plasma 6 Control Agent
===================================

Sencillo por defecto, pero potente cuando hace falta.
"""

import asyncio
import sys
import time
from typing import Any

from structlog import get_logger

from k_pilot import config
from k_pilot.application.deps import AppDeps
from k_pilot.infrastructure.adapters.kwin_windows import KWinWindowAdapter
from k_pilot.infrastructure.adapters.mpris_media import MprisMediaAdapter
from k_pilot.infrastructure.adapters.notifications import FreedesktopNotificationAdapter
from k_pilot.infrastructure.adapters.wake_word import LocalWakeAdapter
from k_pilot.infrastructure.agents.gemini_live import GeminiLiveServer

logger = get_logger()


async def run_app() -> None:
    deps = AppDeps(
        notification_port=FreedesktopNotificationAdapter(),
        window_port=KWinWindowAdapter(),
        media_port=MprisMediaAdapter(),
    )

    server = GeminiLiveServer(deps=deps)
    wake_word = LocalWakeAdapter(reference_folder="assets/hey k-pilot")

    loop = asyncio.get_running_loop()
    background_tasks = set()
    last_trigger_time = 0.0

    def on_wake_word_callback(_detection: dict[str, Any], _stream: Any) -> None:
        nonlocal last_trigger_time
        now = time.monotonic()
        if now - last_trigger_time < 3.0:
            return
        last_trigger_time = now

        def _schedule_task() -> None:
            task = asyncio.create_task(server.trigger_voice_activation())
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
            
            def _log_if_failed(t: asyncio.Task[Any]) -> None:
                if not t.cancelled() and t.exception():
                    logger.error("wake_activation_task_failed", error=str(t.exception()))
            task.add_done_callback(_log_if_failed)

        loop.call_soon_threadsafe(_schedule_task)

    await wake_word.start_listening(on_wake_word_callback)

    try:
        logger.info("k_pilot_started", status="listening")
        await server.start()
    finally:
        await wake_word.stop_listening()
        logger.info("k_pilot_shutdown", detail="cleanup_complete")


def main():
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        logger.fatal("application_crashed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

__all__ = ["config"]
