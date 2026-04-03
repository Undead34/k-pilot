from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from k_pilot.core.application.app_deps import AppDeps


@runtime_checkable
class KPilotSkill(Protocol):
    @classmethod
    def name(cls) -> str:
        """Nombre único para logs/debug."""
        ...

    @classmethod
    def is_available(cls) -> bool:
        """¿Funciona en este sistema?"""
        ...

    @classmethod
    def setup_deps(cls, deps: "AppDeps") -> None:
        """
        Registra los adaptadores Driven en el contenedor dinámico.
        Ej: deps.register(MediaControlPort, MprisMediaAdapter())
        """
        ...

    @classmethod
    def register_tools(cls, agent: "Agent[AppDeps]") -> None:
        """Registra las tools (Driving Adapters) en el agente."""
        ...
