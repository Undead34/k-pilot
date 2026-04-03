from dataclasses import dataclass
from enum import IntEnum


class PlayerPriority(IntEnum):
    """
    Priority levels for media player selection.

    Higher values indicate higher preference when auto-selecting players.
    """

    SPOTIFY = 100  #: Preferred desktop client
    ELISA = 90  #: KDE default music player
    VLC = 80  #: Popular multimedia player
    PLASMA_BROWSER = 70  #: Browser integration extension
    FIREFOX = 60  #: Web browser media sessions
    BRAVE = 50  #: Chromium-based browser
    CHROMIUM = 40  #: Base Chromium browser
    GENERIC = 10  #: Fallback for unknown players


@dataclass(frozen=True, slots=True)
class PlayerIdentifier:
    """
    Immutable identifier for an MPRIS2 media player.

    Attributes:
        bus_name: Full D-Bus service name (e.g., 'org.mpris.MediaPlayer2.spotify').
        priority: Priority level for selection algorithms.
        display_name: Human-readable player name.
    """

    bus_name: str
    priority: int
    display_name: str

    @property
    def base_name(self) -> str:
        """Extract base player name without MPRIS prefix or instance suffix."""
        base = self.bus_name.removeprefix("org.mpris.MediaPlayer2.")
        return base.split(".instance")[0]

    @classmethod
    def from_bus_name(cls, bus_name: str) -> "PlayerIdentifier":
        """
        Create identifier from raw D-Bus service name.

        Args:
            bus_name: Full D-Bus service name.

        Returns:
            Configured PlayerIdentifier with appropriate priority and display name.
        """
        base = bus_name.removeprefix("org.mpris.MediaPlayer2.").split(".instance")[0]
        priority = cls._resolve_priority(base)
        display = cls._resolve_display_name(base)
        return cls(bus_name=bus_name, priority=priority, display_name=display)

    @staticmethod
    def _resolve_priority(base_name: str) -> int:
        """Map base name to priority level."""
        mapping: dict[str, int] = {
            "spotify": PlayerPriority.SPOTIFY,
            "elisa": PlayerPriority.ELISA,
            "vlc": PlayerPriority.VLC,
            "plasma-browser-integration": PlayerPriority.PLASMA_BROWSER,
            "firefox": PlayerPriority.FIREFOX,
            "brave": PlayerPriority.BRAVE,
            "chromium": PlayerPriority.CHROMIUM,
        }
        return mapping.get(base_name.lower(), PlayerPriority.GENERIC)

    @staticmethod
    def _resolve_display_name(base_name: str) -> str:
        """Map base name to human-readable display name."""
        mapping: dict[str, str] = {
            "spotify": "Spotify",
            "elisa": "Elisa",
            "vlc": "VLC",
            "firefox": "Firefox",
            "brave": "Brave",
            "chromium": "Chromium",
            "plasma-browser-integration": "Web Browser",
        }
        return mapping.get(base_name.lower(), base_name.capitalize())
