import shutil
import sys

from pydantic_ai import Agent

from k_pilot.core.application.app_deps import AppDeps
from k_pilot.core.application.skill_protocol import KPilotSkill


class WindowSkill(KPilotSkill):
    @classmethod
    def name(cls) -> str:
        return "window_management"

    @classmethod
    def is_available(cls) -> bool:
        """
        Para manejar ventanas necesitamos:
        1. Estar en Linux.
        2. Tener kdotool instalado en el PATH.
        """
        if sys.platform != "linux":
            return False

        return shutil.which("kdotool") is not None

    @classmethod
    def setup_deps(cls, deps: AppDeps) -> None:
        from k_pilot.core.application.ports.driven import WindowManagerPort

        # En Linux usamos el adaptador de KWin que depende de kdotool
        if sys.platform == "linux":
            from k_pilot.adapters.driven.kwin.kwin_adapter import KWinWindowAdapter

            deps.register(WindowManagerPort, KWinWindowAdapter())

    @classmethod
    def register_tools(cls, agent: Agent[AppDeps]) -> None:
        from k_pilot.adapters.driving.agent_tools import window

        # Registramos las herramientas específicas de ventanas
        agent.tool(window.list_windows)
        agent.tool(window.focus_window)
        agent.tool(window.close_window)
