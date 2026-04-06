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
