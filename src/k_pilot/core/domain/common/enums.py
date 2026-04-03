"""Enums del dominio."""

from enum import Enum, StrEnum, auto


class Priority(Enum):
    LOW = auto()
    NORMAL = auto()
    HIGH = auto()
    CRITICAL = auto()


class PlaybackStatus(StrEnum):
    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"


class RepeatMode(StrEnum):
    NONE = "None"
    TRACK = "Track"
    PLAYLIST = "Playlist"


class ShuffleMode(StrEnum):
    OFF = "Off"
    ON = "On"
