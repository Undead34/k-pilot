from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol

from gi.repository import GLib  # type: ignore[import]


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
