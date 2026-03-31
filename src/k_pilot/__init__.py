"""
K-Pilot: Agente de control para KDE Plasma 6 con Gemini Live.
"""

import asyncio
import sys

from structlog import get_logger

from k_pilot import config
from k_pilot.application.deps import AppDeps
from k_pilot.infrastructure.adapters.kwin_windows import KWinWindowAdapter
from k_pilot.infrastructure.adapters.mpris_media import MprisMediaAdapter
from k_pilot.infrastructure.adapters.notifications import FreedesktopNotificationAdapter
from k_pilot.infrastructure.adapters.wake_word import LocalWakeAdapter

# Importamos tu nuevo servidor Live
from k_pilot.infrastructure.agents.gemini_live import GeminiLiveServer

logger = get_logger()


def main():
    # 1. Armamos las dependencias locales de KDE
    deps = AppDeps(
        notification_port=FreedesktopNotificationAdapter(),
        window_port=KWinWindowAdapter(),
        media_port=MprisMediaAdapter(),
    )

    # 2. Instanciamos Servidor y WakeWord separadamente
    server = GeminiLiveServer(deps=deps)
    wake_word = LocalWakeAdapter(reference_folder="assets/hey k-pilot")

    async def main_loop():
        loop = asyncio.get_running_loop()

        def on_wake_word(detection: dict, raw_stream: getattr(sys, "Any", object)): 
            # Wake word se ejecuta en otro hilo, disparamos tarea asíncrona safe
            loop.call_soon_threadsafe(lambda: asyncio.create_task(server.trigger_voice_activation()))

        await wake_word.start_listening(on_wake_word)

        try:
            await server.start()
        finally:
            await wake_word.stop_listening()

    # 3. Arrancamos
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, asyncio.exceptions.CancelledError):
        print("\n👋 Apagando Gemini Live...")
        sys.exit(0)
    except Exception as e:
        logger.error("fatal_error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

__all__ = ["config"]
