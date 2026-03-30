from dataclasses import dataclass

from k_pilot.domain.ports import (
    MediaControlPort,
    NotificationPort,
    WindowManagerPort,
)


@dataclass(frozen=True)
class AppDeps:
    """Inyección de dependencias para el agente."""

    notification_port: NotificationPort
    window_port: WindowManagerPort
    media_port: MediaControlPort
