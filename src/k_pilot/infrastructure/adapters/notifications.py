"""Adapter para notificaciones Freedesktop unificado con dasbus."""

from typing import Any

from dasbus.connection import SessionMessageBus
from gi.repository import GLib  # type: ignore

from k_pilot.domain.models import Notification, Priority, Result
from k_pilot.domain.ports import NotificationPort
from k_pilot.infrastructure.logging import get_logger

logger = get_logger(layer="infrastructure", component="notification_adapter")


class FreedesktopNotificationAdapter(NotificationPort):
    """Implementación via org.freedesktop.Notifications usando dasbus."""

    SERVICE_NAME = "org.freedesktop.Notifications"
    OBJECT_PATH = "/org/freedesktop/Notifications"

    def __init__(self, bus: SessionMessageBus | None = None):
        self._bus = bus or SessionMessageBus()
        self._proxy = None
        self._connect()

    def _connect(self):
        try:
            self._proxy = self._bus.get_proxy(
                service_name=self.SERVICE_NAME,
                object_path=self.OBJECT_PATH,
            )
            logger.info("notification_adapter.connected", backend="dasbus")
        except Exception as e:
            logger.exception("notification_adapter.connect_failed", error=str(e))
            self._proxy: Any = None

    def is_available(self) -> bool:
        return self._proxy is not None

    def send(self, notification: Notification) -> Result:
        if not self.is_available():
            logger.warning("notification_adapter.unavailable")
            return Result(False, "Servicio de notificaciones no disponible")

        try:
            urgency_map = {
                Priority.LOW: 0,
                Priority.NORMAL: 1,
                Priority.HIGH: 2,
                Priority.CRITICAL: 2,
            }

            urgency_value = urgency_map.get(notification.priority, 1)

            hints = {
                "urgency": GLib.Variant("y", urgency_value),
                "desktop-entry": GLib.Variant("s", "k-pilot"),
            }

            if notification.priority == Priority.CRITICAL:
                hints["resident"] = GLib.Variant("b", True)

            # Llamada D-Bus nativa con dasbus
            nid = self._proxy.Notify(
                "K-Pilot",  # app_name
                0,  # replaces_id
                notification.icon,  # app_icon
                notification.title,  # summary
                notification.body,  # body
                [],  # actions
                hints,  # hints
                notification.timeout_ms,  # expire_timeout
            )

            logger.info(
                "notification.sent",
                title=notification.title,
                priority=notification.priority.name,
                id=nid,
                has_icon=bool(notification.icon),
            )

            return Result(
                True,
                f"Notificación enviada (ID: {nid})",
                data={"notification_id": nid},
            )

        except Exception as e:
            logger.exception("notification.unexpected_error", error=str(e))
            return Result(False, f"Error: {str(e)}")
