from dataclasses import dataclass

from k_pilot.core.domain.common.enums import PlaybackStatus, RepeatMode, ShuffleMode


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
    volume: float = 1.0
    can_seek: bool = False
    can_go_next: bool = True
    can_go_previous: bool = True


@dataclass(frozen=True)
class MediaPlayer:
    bus_name: str
    name: str
    is_active: bool
