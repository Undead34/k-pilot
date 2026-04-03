import sys

from pydantic_ai import Agent

from k_pilot.core.application.app_deps import AppDeps
from k_pilot.core.application.skill_protocol import KPilotSkill


class NotificationSkill(KPilotSkill):
    @classmethod
    def name(cls) -> str:
        return "notification_feature"

    @classmethod
    def is_available(cls) -> bool:
        # En Linux siempre intentamos usar D-Bus para notificaciones
        return sys.platform == "linux"

    @classmethod
    def setup_deps(cls, deps: AppDeps) -> None:
        from k_pilot.core.application.ports.driven import NotificationPort

        if sys.platform == "linux":
            from k_pilot.adapters.driven.notifications.freedesktop_notification_adapter import (
                FreedesktopNotificationAdapter,
            )

            # Registramos el puerto de NOTIFICACIONES con su adaptador real
            deps.register(NotificationPort, FreedesktopNotificationAdapter())

    @classmethod
    def register_tools(cls, agent: Agent[AppDeps]) -> None:
        from k_pilot.adapters.driving.agent_tools import notification

        # Registramos la herramienta de notificaciones
        agent.tool(notification.notify_user)
