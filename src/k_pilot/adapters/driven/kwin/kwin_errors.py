class KWinAdapterError(Exception):
    """Base exception for KWin adapter operations."""

    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        window_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.window_id = window_id


class KdotoolNotFoundError(KWinAdapterError):
    """Raised when kdotool executable is not available."""

    pass


class WindowOperationError(KWinAdapterError):
    """Raised when a window operation fails."""

    pass
