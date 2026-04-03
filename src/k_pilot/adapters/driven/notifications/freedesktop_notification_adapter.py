# Copyright 2026 K-Pilot Contributors
# SPDX-License-Identifier: MIT
# pylint: disable=too-few-public-methods

"""
D-Bus notification adapter implementing the Freedesktop Notifications specification.

This module provides a production-ready adapter for sending desktop notifications
via the org.freedesktop.Notifications service using dasbus. It implements the
NotificationPort interface from the domain layer.

References:
    - Freedesktop Notifications Spec: https://specifications.freedesktop.org/notification-spec/
    - dasbus documentation: https://dasbus.readthedocs.io/
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol, cast

import structlog
from dasbus.connection import SessionMessageBus
from gi.repository import GLib  # type: ignore[import]

from k_pilot.core.application.ports.driven import NotificationPort
from k_pilot.core.domain import Notification, Priority, Result

if TYPE_CHECKING:
    from dasbus.client.proxy import InterfaceProxy


class NotificationProxy(Protocol):
    """Protocol for D-Bus Notifications interface."""

    def Notify(
        self,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        summary: str,
        body: str,
        actions: list[str],
        hints: dict[str, Any],
        expire_timeout: int,
    ) -> int: ...


logger = structlog.get_logger("k-pilot.notification_adapter")


class NotificationError(Exception):
    """Base exception for notification-related errors."""

    def __init__(self, message: str, *, original_error: Exception | None = None) -> None:
        super().__init__(message)
        self.original_error = original_error


class NotificationServiceUnavailableError(NotificationError):
    """Raised when the D-Bus notification service is not available."""

    pass


class NotificationSendError(NotificationError):
    """Raised when sending a notification fails."""

    pass


class UrgencyLevel(IntEnum):
    """
    Urgency levels as defined by Freedesktop Notifications spec.

    See: https://specifications.freedesktop.org/notification-spec/latest/ar01s09.html
    """

    LOW = 0  #: Low urgency. No notification required if user is busy.
    NORMAL = 1  #: Normal urgency. Visual notification expected.
    CRITICAL = 2  #: Critical urgency. Notification should not expire.


@dataclass(frozen=True, slots=True)
class NotificationHints:
    """
    Immutable container for D-Bus notification hints.

    Attributes:
        urgency: The urgency level of the notification.
        desktop_entry: Desktop file identifier without .desktop extension.
        resident: Whether the notification should remain visible after action.
    """

    urgency: UrgencyLevel
    desktop_entry: str
    resident: bool = False

    def as_glib_dict(self) -> dict[str, Any]:
        """
        Convert hints to GLib.Variant dictionary for D-Bus transmission.

        Returns:
            Dictionary mapping hint keys to GLib.Variant values.
        """
        hints: dict[str, Any] = {
            "urgency": GLib.Variant("y", int(self.urgency)),
            "desktop-entry": GLib.Variant("s", self.desktop_entry),
        }

        if self.resident:
            hints["resident"] = GLib.Variant("b", True)

        return hints


class FreedesktopNotificationAdapter(NotificationPort):
    """
    Freedesktop Notifications using dasbus.

    This adapter implements the NotificationPort interface to provide desktop
    notification capabilities via D-Bus. It handles connection management,
    priority mapping, and proper error handling.

    Thread Safety:
        This class is not thread-safe. Instances should be confined to a single
        thread or externally synchronized.

    Example:
    ```python
    adapter = FreedesktopNotificationAdapter()
    if adapter.is_available():
        result = adapter.send(Notification(
            title="Test",
            body="Hello World",
            priority=Priority.NORMAL
        ))
        print(result.success)  # True
    ````

    Attributes:
        SERVICE_NAME: D-Bus service name for notifications (class constant).
        OBJECT_PATH: D-Bus object path for notification interface (class constant).
        INTERFACE_NAME: D-Bus interface name for notifications (class constant).
    """

    # Class constants following Freedesktop spec
    SERVICE_NAME: ClassVar[str] = "org.freedesktop.Notifications"
    OBJECT_PATH: ClassVar[str] = "/org/freedesktop/Notifications"
    INTERFACE_NAME: ClassVar[str] = "org.freedesktop.Notifications"

    # Priority mapping: Domain Priority -> Spec UrgencyLevel
    _PRIORITY_MAP: ClassVar[dict[Priority, UrgencyLevel]] = {
        Priority.LOW: UrgencyLevel.LOW,
        Priority.NORMAL: UrgencyLevel.NORMAL,
        Priority.HIGH: UrgencyLevel.CRITICAL,
        Priority.CRITICAL: UrgencyLevel.CRITICAL,
    }

    # Application identifier for .desktop file association
    _DESKTOP_ENTRY: ClassVar[str] = "k-pilot"

    def __init__(self, bus: SessionMessageBus | None = None) -> None:
        """
        Initialize the notification adapter.

        Creates a new adapter instance. If no bus is provided, a new
        SessionMessageBus connection will be established.

        Args:
            bus: Optional pre-configured D-Bus session bus connection.

        Note:
            Connection establishment happens lazily on first use if not
            explicitly provided.
        """
        self._bus: Final[SessionMessageBus] = bus or SessionMessageBus()
        self._proxy: NotificationProxy | None = None
        self._connect()

    def _connect(self) -> None:
        """
        Establish connection to the D-Bus notification service.

        Attempts to obtain a proxy object for the notification service.
        If the service is unavailable, the proxy remains None and
        is_available() will return False.

        Logs:
            INFO: On successful connection with backend details.
            ERROR: On connection failure with exception details.
        """
        try:
            self._proxy = cast(
                "NotificationProxy",
                self._bus.get_proxy(
                    service_name=self.SERVICE_NAME,
                    object_path=self.OBJECT_PATH,
                    interface_name=self.INTERFACE_NAME,
                ),
            )
            logger.info(
                "notification_adapter.connected",
                backend="dasbus",
                service=self.SERVICE_NAME,
            )
        except Exception as exc:
            logger.exception(
                "notification_adapter.connect_failed",
                error=str(exc),
                service=self.SERVICE_NAME,
            )
            self._proxy = None

    def is_available(self) -> bool:
        """
        Check if the notification service is available.

        Returns:
            True if the D-Bus proxy is connected and ready for use,
            False otherwise.
        """
        return self._proxy is not None

    def _map_priority(self, priority: Priority) -> UrgencyLevel:
        """
        Map domain Priority to Freedesktop UrgencyLevel.

        Args:
            priority: The domain model priority level.

        Returns:
            Corresponding UrgencyLevel for D-Bus transmission.

        Note:
            Falls back to NORMAL if priority is unrecognized.
        """
        return self._PRIORITY_MAP.get(priority, UrgencyLevel.NORMAL)

    def _build_hints(self, notification: Notification) -> NotificationHints:
        """
        Construct notification hints based on notification properties.

        Args:
            notification: The notification to build hints for.

        Returns:
            Configured NotificationHints instance.
        """
        urgency = self._map_priority(notification.priority)

        # Critical notifications should remain visible until dismissed
        is_resident = notification.priority == Priority.CRITICAL

        return NotificationHints(
            urgency=urgency,
            desktop_entry=self._DESKTOP_ENTRY,
            resident=is_resident,
        )

    def send(self, notification: Notification) -> Result:
        """
        Send a desktop notification via D-Bus.

        Transforms the domain Notification into a Freedesktop-compliant
        D-Bus call and transmits it to the notification daemon.

        Args:
            notification: The notification to display. Must contain at
                minimum a title and body.

        Returns:
            Result object indicating success/failure with optional data
            containing the notification ID returned by the server.

        Raises:
            NotificationServiceUnavailableError: If the service is not available.
                Note: This is caught internally and returned as Result(False, ...).

        Example:
            >>> notification = Notification(
            ...     title="Backup Complete",
            ...     body="Files synced successfully",
            ...     priority=Priority.NORMAL,
            ...     icon="dialog-information"
            ... )
            >>> result = adapter.send(notification)
            >>> if result.success:
            ...     print(f"Notification ID: {result.data['notification_id']}")
        """
        if not self.is_available():
            logger.warning(
                "notification_adapter.unavailable",
                attempted_send=True,
            )
            return Result(
                success=False,
                message="Notification service unavailable (D-Bus service not reachable)",
            )

        # Type narrowing: mypy knows self._proxy is not None here
        proxy = cast("NotificationProxy", self._proxy)

        try:
            hints = self._build_hints(notification)

            # Prepare D-Bus arguments following spec order:
            # Notify(app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout)
            notification_id: int = proxy.Notify(
                self._DESKTOP_ENTRY,  # app_name
                0,  # replaces_id (0 = new notification)
                notification.icon or "",  # app_icon (empty string if None)
                notification.title,  # summary
                notification.body,  # body
                [],  # actions (not implemented)
                hints.as_glib_dict(),  # hints
                notification.timeout_ms,  # expire_timeout
            )

            logger.info(
                "notification.sent",
                title=notification.title,
                priority=notification.priority.name,
                notification_id=notification_id,
                has_icon=bool(notification.icon),
                urgency=hints.urgency.name,
            )

            return Result(
                success=True,
                message=f"Notification delivered successfully (ID: {notification_id})",
                data={"notification_id": notification_id},
            )

        except Exception as exc:
            logger.exception(
                "notification.send_failed",
                error=str(exc),
                title=notification.title,
                priority=notification.priority.name,
            )
            return Result(
                success=False,
                message=f"Failed to send notification: {str(exc)}",
            )

    @contextlib.contextmanager
    def _connection_context(self):
        """
        Context manager for temporary D-Bus connections.

        Yields:
            InterfaceProxy if available, None otherwise.

        Note:
            Currently unused but provided for future resource management
            and testing purposes.
        """
        try:
            yield self._proxy
        finally:
            # Cleanup logic if needed in future
            pass
