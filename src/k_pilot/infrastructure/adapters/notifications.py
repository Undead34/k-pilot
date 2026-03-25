"""Adapter para notificaciones Freedesktop (funciona en KDE)."""

import structlog
from gi.repository import GLib  # Asegúrate de importar GLib aquí
from pydbus import SessionBus

from k_pilot.domain.models import Notification, Priority, Result
from k_pilot.domain.ports import NotificationPort

logger = structlog.get_logger()


class FreedesktopNotificationAdapter(NotificationPort):
    """Implementación via org.freedesktop.Notifications."""

    INTERFACE = "org.freedesktop.Notifications"
    PATH = "/org/freedesktop/Notifications"

    def __init__(self, bus: SessionBus):
        self._bus = bus
        self._proxy = None
        self._connect()

    def _connect(self):
        try:
            self._proxy = self._bus.get(self.INTERFACE, self.PATH)
            logger.info("notification_adapter.connected")
        except Exception as e:
            logger.error("notification_adapter.connect_failed", error=str(e))
            self._proxy = None

    def is_available(self) -> bool:
        return self._proxy is not None

    def send(self, notification: Notification) -> Result:
        if not self.is_available():
            return Result(False, "Servicio de notificaciones no disponible")

        try:
            # Mapear Priority a urgencia Freedesktop (0=low, 1=normal, 2=critical)
            urgency_map = {
                Priority.LOW: 0,
                Priority.NORMAL: 1,
                Priority.HIGH: 2,
                Priority.CRITICAL: 2,
            }

            # IMPORTANTE: Los hints deben ser GLib.Variant, no tipos Python crudos
            urgency_value = urgency_map.get(notification.priority, 1)

            hints = {
                "urgency": GLib.Variant("y", urgency_value),  # 'y' = byte/uint8
                "desktop-entry": GLib.Variant("s", "k-pilot"),  # 's' = string
            }

            # Si es crítico, añadir hint específico de KDE para persistencia
            if notification.priority == Priority.CRITICAL:
                hints["resident"] = GLib.Variant("b", True)  # 'b' = boolean

            # Llamada D-Bus con tipos correctos
            nid = self._proxy.Notify(
                "K-Pilot",  # app_name (string)
                0,  # replaces_id (uint32)
                notification.icon,  # app_icon (string)
                notification.title,  # summary (string)
                notification.body,  # body (string)
                [],  # actions (array de strings)
                hints,  # hints (dict string->variant)
                notification.timeout_ms,  # expire_timeout (int32)
            )

            logger.info(
                "notification.sent",
                title=notification.title,
                priority=notification.priority.name,
                id=nid,
            )

            return Result(
                success=True,
                message=f"Notificación enviada (ID: {nid})",
                data={"notification_id": nid},
            )

        except GLib.Error as e:
            logger.error("notification.dbus_error", error=e.message)
            return Result(False, f"Error D-Bus: {e.message}")
        except Exception as e:
            logger.error("notification.unexpected_error", error=str(e))
            return Result(False, f"Error: {str(e)}")
