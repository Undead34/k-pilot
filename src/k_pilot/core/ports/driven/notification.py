from typing import Protocol, runtime_checkable

from k_pilot.core.domain import Notification, Result


@runtime_checkable
class NotificationPort(Protocol):
    """Puerto para envío de notificaciones del sistema."""

    def send(self, notification: Notification) -> Result: ...
    def is_available(self) -> bool: ...
