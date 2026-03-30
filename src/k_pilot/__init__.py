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

    # 2. Se las inyectamos al servidor de Gemini Live
    server = GeminiLiveServer(deps=deps)

    # 3. Arrancamos
    try:
        asyncio.run(server.start())
    except (KeyboardInterrupt, asyncio.exceptions.CancelledError):
        print("\n👋 Apagando Gemini Live...")
        sys.exit(0)
    except Exception as e:
        logger.error("fatal_error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

__all__ = ["config"]
