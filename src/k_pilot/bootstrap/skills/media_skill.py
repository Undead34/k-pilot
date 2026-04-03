import sys

from pydantic_ai import Agent

from k_pilot.core.application.app_deps import AppDeps
from k_pilot.core.application.skill_protocol import KPilotSkill


class MediaSkill(KPilotSkill):
    @classmethod
    def name(cls) -> str:
        return "media_control"

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "linux"  # Capacidad técnica, no estado de Spotify

    @classmethod
    def setup_deps(cls, deps: "AppDeps") -> None:
        from k_pilot.adapters.driven.mpris.mpris_media_adapter import MprisMediaAdapter
        from k_pilot.core.application.ports.driven import MediaControlPort

        deps.register(MediaControlPort, MprisMediaAdapter())

    @classmethod
    def register_tools(cls, agent: "Agent[AppDeps]") -> None:
        from k_pilot.adapters.driving.agent_tools import media

        agent.tool(media.control_media)
        agent.tool(media.get_now_playing)
        agent.tool(media.list_media_players)
