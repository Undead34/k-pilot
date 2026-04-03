"""Dominio de K-Pilot."""

from k_pilot.core.domain.common.enums import PlaybackStatus, Priority, RepeatMode, ShuffleMode
from k_pilot.core.domain.common.value_objects import Result, WindowRect
from k_pilot.core.domain.media.models import MediaInfo, MediaPlayer
from k_pilot.core.domain.notification.models import Notification
from k_pilot.core.domain.window.models import WindowInfo

__all__ = [
    # Enums
    "Priority",
    "PlaybackStatus",
    "RepeatMode",
    "ShuffleMode",
    # Models
    "Result",
    "WindowRect",
    "Notification",
    "WindowInfo",
    "MediaInfo",
    "MediaPlayer",
]
