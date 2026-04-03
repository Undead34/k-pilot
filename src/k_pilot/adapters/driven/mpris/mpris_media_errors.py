class MprisError(Exception):
    """Base exception for MPRIS2 adapter errors."""

    def __init__(self, message: str, *, player: str | None = None) -> None:
        super().__init__(message)
        self.player = player


class PlayerNotFoundError(MprisError):
    """Raised when no suitable media player is found."""

    pass


class DbusOperationError(MprisError):
    """Raised when a D-Bus operation fails."""

    pass
