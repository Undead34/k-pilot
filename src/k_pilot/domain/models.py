"""Modelos de datos inmutables del dominio."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class Priority(Enum):
    LOW = auto()
    NORMAL = auto()
    HIGH = auto()
    CRITICAL = auto()


class PlaybackStatus(Enum):
    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"


class RepeatMode(Enum):
    NONE = "None"
    TRACK = "Track"
    PLAYLIST = "Playlist"


class ShuffleMode(Enum):
    OFF = "Off"
    ON = "On"


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    priority: Priority = Priority.NORMAL
    icon: str = ""  # None
    timeout_ms: int = 5000


@dataclass(frozen=True)
class WindowInfo:
    id: str
    title: str
    app_name: str
    is_active: bool
    is_minimized: bool
    desktop: int | None = None


@dataclass(frozen=True)
class Result:
    success: bool
    message: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class MediaInfo:
    title: str
    artist: str
    album: str | None = None
    player_name: str = ""
    status: PlaybackStatus = PlaybackStatus.STOPPED
    position_ms: int | None = None
    length_ms: int | None = None
    artwork_url: str | None = None
    repeat_mode: RepeatMode = RepeatMode.NONE
    shuffle_mode: ShuffleMode = ShuffleMode.OFF
    volume: float = 1.0  # 0.0 a 1.0
    can_seek: bool = False
    can_go_next: bool = True
    can_go_previous: bool = True


@dataclass(frozen=True)
class MediaPlayer:
    bus_name: str
    name: str
    is_active: bool
