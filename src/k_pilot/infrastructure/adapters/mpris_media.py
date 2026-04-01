# Copyright 2026 K-Pilot Contributors
# SPDX-License-Identifier: MIT
# pylint: disable=too-many-public-methods,too-many-instance-attributes

"""
MPRIS2 Media Player adapter for Linux desktop integration.

This module provides an adapter implementing the MediaControlPort
interface via the MPRIS2 D-Bus specification. It supports player discovery,
priority-based selection, and comprehensive media control operations.

References:
    - MPRIS2 Specification: https://specifications.freedesktop.org/mpris-spec/latest/
    - dasbus documentation: https://dasbus.readthedocs.io/
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

from dasbus.connection import SessionMessageBus
from dasbus.error import DBusError
from gi.repository import GLib  # type: ignore[import]

from k_pilot.domain.models import (
    MediaInfo,
    MediaPlayer,
    PlaybackStatus,
    RepeatMode,
    Result,
    ShuffleMode,
)
from k_pilot.domain.ports import MediaControlPort
from k_pilot.infrastructure.logging import get_logger

if TYPE_CHECKING:
    from dasbus.client.proxy import InterfaceProxy

logger = get_logger(layer="infrastructure", component="mpris_adapter")


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


class MprisMediaAdapter(MediaControlPort):
    """
    Production-grade MPRIS2 media control adapter.

    This adapter implements the MediaControlPort interface to provide comprehensive
    media player control via the MPRIS2 D-Bus specification. It features:

    - Automatic player discovery and priority-based selection
    - Fault tolerance with circuit-breaker pattern for failing players
    - Comprehensive metadata extraction and normalization
    - Support for both desktop and browser-based media sessions

    Thread Safety:
        This class is not thread-safe. All operations should be confined to the
        same thread or externally synchronized.

    Example:
        >>> adapter = MprisMediaAdapter()
        >>> if adapter.is_available():
        ...     result = await adapter.toggle_playback()
        ...     print(result.message)  # "⏯️ Playback toggled"
    """

    # D-Bus constants following MPRIS2 specification
    PLAYER_PATH: ClassVar[Final[str]] = "/org/mpris/MediaPlayer2"
    PLAYER_IFACE: ClassVar[Final[str]] = "org.mpris.MediaPlayer2.Player"
    PROPERTIES_IFACE: ClassVar[Final[str]] = "org.freedesktop.DBus.Properties"
    DBUS_IFACE: ClassVar[Final[str]] = "org.freedesktop.DBus"
    MPRIS_PREFIX: ClassVar[Final[str]] = "org.mpris.MediaPlayer2."

    # Status mappings: MPRIS2 string -> Domain enum
    _PLAYBACK_STATUS_MAP: ClassVar[Final[dict[str, PlaybackStatus]]] = {
        "Playing": PlaybackStatus.PLAYING,
        "Paused": PlaybackStatus.PAUSED,
        "Stopped": PlaybackStatus.STOPPED,
    }

    _REPEAT_MODE_MAP: ClassVar[Final[dict[str, RepeatMode]]] = {
        "None": RepeatMode.NONE,
        "Track": RepeatMode.TRACK,
        "Playlist": RepeatMode.PLAYLIST,
    }

    def __init__(self, bus: SessionMessageBus | None = None) -> None:
        """
        Initialize the MPRIS2 media adapter.

        Args:
            bus: Optional pre-configured D-Bus session bus. If not provided,
                a new SessionMessageBus connection will be created.
        """
        self._bus: Final[SessionMessageBus] = bus or SessionMessageBus()
        self._last_player: str | None = None
        self._failed_buses: defaultdict[str, int] = defaultdict(int)

        logger.info(
            "media_adapter.initialized",
            backend="mpris2",
            bus_type="session",
        )

    def is_available(self) -> bool:
        """
        Check if D-Bus session is accessible.

        Returns:
            True if the D-Bus daemon is reachable and ListNames() succeeds,
            False otherwise.
        """
        try:
            proxy = self._get_dbus_proxy()
            proxy.ListNames()
            logger.debug("media_adapter.available_check.passed")
            return True
        except Exception as exc:
            logger.warning("media_adapter.available_check.failed", error=str(exc))
            return False

    async def toggle_playback(self, player_name: str | None = None) -> Result:
        """
        Toggle play/pause on the selected player.

        Args:
            player_name: Optional specific player to control. If None, uses
                priority-based auto-selection.

        Returns:
            Result indicating success/failure with player details.
        """
        return await self._send_command(
            method="PlayPause",
            success_message="⏯️ Playback toggled",
            player_name=player_name,
        )

    async def next_track(self, player_name: str | None = None) -> Result:
        """
        Skip to next track.

        Args:
            player_name: Optional specific player to control.

        Returns:
            Result indicating success/failure.
        """
        return await self._send_command(
            method="Next",
            success_message="⏭️ Next track",
            player_name=player_name,
        )

    async def previous_track(
        self,
        force: bool = False,
        player_name: str | None = None,
    ) -> Result:
        """
        Go to previous track with optional force behavior.

        When force=True, seeks to track start first (via SetPosition) to ensure
        actual track change rather than track restart.

        Args:
            force: If True, forces previous track by seeking to start first.
            player_name: Optional specific player to control.

        Returns:
            Result indicating success/failure.
        """
        logger.info(
            "media.previous_track.started",
            force=force,
            player_name=player_name,
        )

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            player_proxy = self._get_player_proxy(player)

            if force:
                await self._force_previous_track(player, player_proxy)

            player_proxy.Previous()

            logger.info(
                "media.previous_track.completed",
                player=self._get_display_name(player),
                force=force,
            )
            return Result(
                success=True,
                message="⏮️ Previous track",
                data={
                    "player": self._get_display_name(player),
                    "force": force,
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, "previous_track")
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, "previous_track")

    async def stop(self, player_name: str | None = None) -> Result:
        """
        Stop playback.

        Args:
            player_name: Optional specific player to control.

        Returns:
            Result indicating success/failure.
        """
        return await self._send_command(
            method="Stop",
            success_message="⏹️ Playback stopped",
            player_name=player_name,
        )

    async def seek(self, position_ms: int, player_name: str | None = None) -> Result:
        """
        Seek to absolute position in milliseconds.

        Uses SetPosition with the current track ID for precise seeking.

        Args:
            position_ms: Target position in milliseconds (clamped to >= 0).
            player_name: Optional specific player to control.

        Returns:
            Result with formatted position string on success.
        """
        logger.info(
            "media.seek_absolute.started",
            position_ms=position_ms,
            player_name=player_name,
        )

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            player_proxy = self._get_player_proxy(player)
            metadata = self._get_metadata(player)
            track_id = metadata.get("mpris:trackid")

            if not track_id:
                return Result(
                    success=False,
                    message="Player does not expose track ID for seeking",
                )

            position_us = max(0, int(position_ms)) * 1000
            player_proxy.SetPosition(track_id, position_us)

            mins, secs = divmod(position_ms // 1000, 60)
            logger.info(
                "media.seek_absolute.completed",
                player=self._get_display_name(player),
                position_ms=position_ms,
            )
            return Result(
                success=True,
                message=f"⏱️ {mins}:{secs:02d}",
                data={
                    "player": self._get_display_name(player),
                    "position_ms": position_ms,
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, "seek")
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, "seek")

    async def skip_forward(
        self,
        offset_ms: int = 10000,
        player_name: str | None = None,
    ) -> Result:
        """
        Skip forward by relative offset.

        Args:
            offset_ms: Milliseconds to skip forward (default: 10s).
            player_name: Optional specific player to control.

        Returns:
            Result indicating success/failure.
        """
        return await self._seek_relative(abs(int(offset_ms)), player_name)

    async def skip_backward(
        self,
        offset_ms: int = 10000,
        player_name: str | None = None,
    ) -> Result:
        """
        Skip backward by relative offset.

        Args:
            offset_ms: Milliseconds to skip backward (default: 10s).
            player_name: Optional specific player to control.

        Returns:
            Result indicating success/failure.
        """
        return await self._seek_relative(-abs(int(offset_ms)), player_name)

    async def get_repeat_mode(self, player_name: str | None = None) -> RepeatMode:
        """
        Get current repeat/loop mode.

        Args:
            player_name: Optional specific player to query.

        Returns:
            Current RepeatMode (defaults to NONE on error).
        """
        player = self._pick_player(player_name)
        if not player:
            return RepeatMode.NONE

        try:
            props = self._get_properties_proxy(player)
            loop_status = str(props.Get(self.PLAYER_IFACE, "LoopStatus"))
            return self._REPEAT_MODE_MAP.get(loop_status, RepeatMode.NONE)

        except Exception as exc:
            logger.warning(
                "media.get_repeat_mode.failed",
                error=str(exc),
                player=player,
            )
            self._mark_failed(player)
            return RepeatMode.NONE

    async def set_repeat_mode(
        self,
        mode: RepeatMode,
        player_name: str | None = None,
    ) -> Result:
        """
        Set repeat/loop mode.

        Args:
            mode: Target repeat mode (NONE, TRACK, PLAYLIST).
            player_name: Optional specific player to control.

        Returns:
            Result with status icon message.
        """
        logger.info(
            "media.set_repeat_mode.started",
            mode=mode.value,
            player_name=player_name,
        )

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            props = self._get_properties_proxy(player)
            props.Set(self.PLAYER_IFACE, "LoopStatus", mode.value)

            icons = {
                RepeatMode.NONE: "🔁 Off",
                RepeatMode.TRACK: "🔂 Track",
                RepeatMode.PLAYLIST: "🔁 Playlist",
            }

            return Result(
                success=True,
                message=icons[mode],
                data={
                    "player": self._get_display_name(player),
                    "mode": mode.value,
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, "set_repeat_mode")
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, "set_repeat_mode")

    async def cycle_repeat_mode(self, player_name: str | None = None) -> Result:
        """
        Cycle through repeat modes: NONE -> TRACK -> PLAYLIST -> NONE.

        Args:
            player_name: Optional specific player to control.

        Returns:
            Result of the set operation.
        """
        current = await self.get_repeat_mode(player_name)
        next_mode = {
            RepeatMode.NONE: RepeatMode.TRACK,
            RepeatMode.TRACK: RepeatMode.PLAYLIST,
            RepeatMode.PLAYLIST: RepeatMode.NONE,
        }[current]
        return await self.set_repeat_mode(next_mode, player_name)

    async def toggle_shuffle(self, player_name: str | None = None) -> Result:
        """
        Toggle shuffle mode on/off.

        Args:
            player_name: Optional specific player to control.

        Returns:
            Result with shuffle status message.
        """
        logger.info("media.toggle_shuffle.started", player_name=player_name)

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            props = self._get_properties_proxy(player)
            current = bool(props.Get(self.PLAYER_IFACE, "Shuffle").unpack())
            new_value = not current
            props.Set(self.PLAYER_IFACE, "Shuffle", new_value)

            status = "🔀 ON" if new_value else "➡️ OFF"
            return Result(
                success=True,
                message=f"Shuffle {status}",
                data={
                    "player": self._get_display_name(player),
                    "shuffle": new_value,
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, "toggle_shuffle")
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, "toggle_shuffle")

    async def set_volume(self, volume: float, player_name: str | None = None) -> Result:
        """
        Set player volume level.

        Args:
            volume: Volume level (0.0 to 1.0, will be clamped).
            player_name: Optional specific player to control.

        Returns:
            Result with percentage message.
        """
        logger.info(
            "media.set_volume.started",
            volume=volume,
            player_name=player_name,
        )

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            clamped = max(0.0, min(1.0, float(volume)))
            props = self._get_properties_proxy(player)
            props.Set(
                self.PLAYER_IFACE,
                "Volume",
                GLib.Variant("d", float(clamped)),
            )

            return Result(
                success=True,
                message=f"🔊 {int(clamped * 100)}%",
                data={
                    "player": self._get_display_name(player),
                    "volume": clamped,
                    "volume_pct": int(clamped * 100),
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, "set_volume")
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, "set_volume")

    async def get_current_track(
        self,
        player_name: str | None = None,
    ) -> MediaInfo | None:
        """
        Get detailed metadata for current track.

        Extracts and normalizes metadata from MPRIS2, handling various
        player implementations and missing fields gracefully.

        Args:
            player_name: Optional specific player to query.

        Returns:
            MediaInfo with track details, or None if unavailable.
        """
        logger.debug("media.get_current_track.started", player_name=player_name)

        player = self._pick_player(player_name)
        if not player:
            return None

        try:
            return self._extract_media_info(player)
        except Exception as exc:
            logger.warning(
                "media.get_current_track.failed",
                error=str(exc),
                player=player,
            )
            self._mark_failed(player)
            return None

    async def list_players(self) -> list[MediaPlayer]:
        """
        List all available MPRIS2 media players.

        Discovers players, filters out failing ones, and marks active/playing
        status based on current selection and playback state.

        Returns:
            List of MediaPlayer objects sorted by priority.
        """
        logger.info("media.list_players.started")

        bus_names = self._list_mpris_buses()
        if not bus_names:
            self._last_player = None
            self._failed_buses.clear()
            logger.info("media.list_players.empty")
            return []

        # Clean up failed buses that no longer exist
        alive = set(bus_names)
        self._failed_buses = defaultdict(
            int,
            {
                name: count
                for name, count in self._failed_buses.items()
                if name in alive
            },
        )

        current = self._pick_player()
        players: list[MediaPlayer] = []

        for bus_name in bus_names:
            try:
                proxy = self._get_properties_proxy(bus_name)
                status_raw = str(proxy.Get(self.PLAYER_IFACE, "PlaybackStatus"))
                is_active = bus_name == current or status_raw == "Playing"

                identifier = PlayerIdentifier.from_bus_name(bus_name)
                players.append(
                    MediaPlayer(
                        bus_name=bus_name,
                        name=identifier.display_name,
                        is_active=is_active,
                    )
                )
            except Exception as exc:
                logger.debug("media.list_players.skip", player=bus_name, error=str(exc))
                self._mark_failed(bus_name)

        logger.info("media.list_players.completed", count=len(players))
        return players

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

    def _get_dbus_proxy(self) -> InterfaceProxy:
        """Get proxy for D-Bus daemon itself."""
        return cast(
            "InterfaceProxy",
            self._bus.get_proxy(
                service_name=self.DBUS_IFACE,
                object_path="/org/freedesktop/DBus",
                interface_name=self.DBUS_IFACE,
            ),
        )

    def _get_player_proxy(self, bus_name: str) -> InterfaceProxy:
        """Get player control interface proxy."""
        return cast(
            "InterfaceProxy",
            self._bus.get_proxy(
                service_name=bus_name,
                object_path=self.PLAYER_PATH,
                interface_name=self.PLAYER_IFACE,
            ),
        )

    def _get_properties_proxy(self, bus_name: str) -> InterfaceProxy:
        """Get properties interface proxy for the player."""
        return cast(
            "InterfaceProxy",
            self._bus.get_proxy(
                service_name=bus_name,
                object_path=self.PLAYER_PATH,
                interface_name=self.PROPERTIES_IFACE,
            ),
        )

    def _list_mpris_buses(self) -> list[str]:
        """
        Discover all MPRIS2 service names on the session bus.

        Returns:
            List of bus names sorted by priority (highest first).
        """
        try:
            proxy = self._get_dbus_proxy()
            names = proxy.ListNames()
            mpris_buses = [
                str(name) for name in names if str(name).startswith(self.MPRIS_PREFIX)
            ]

            # Sort by priority (highest first)
            sorted_buses = sorted(
                mpris_buses,
                key=lambda name: PlayerIdentifier.from_bus_name(name).priority,
                reverse=True,
            )

            logger.debug("media.discovered_buses", count=len(sorted_buses))
            return sorted_buses

        except Exception as exc:
            logger.warning("media.list_buses.failed", error=str(exc))
            return []

    def _pick_player(self, player_hint: str | None = None) -> str | None:
        """
        Select best player based on hint, last used, or priority.

        Selection algorithm:
        1. If hint provided: match by display name or bus name
        2. If last player still healthy: reuse it
        3. Find first playing player by priority
        4. Fallback to highest priority available

        Args:
            player_hint: Optional name substring to match.

        Returns:
            Selected bus name or None if no players available.
        """
        buses = self._list_mpris_buses()
        if not buses:
            self._last_player = None
            return None

        healthy = [b for b in buses if b not in self._failed_buses]
        candidates = healthy or buses

        # 1. Hint-based selection
        if player_hint:
            hint_lower = player_hint.lower()
            for bus_name in candidates:
                identifier = PlayerIdentifier.from_bus_name(bus_name)
                if (
                    hint_lower in identifier.display_name.lower()
                    or hint_lower in identifier.base_name
                ):
                    self._last_player = bus_name
                    logger.info(
                        "media.player_selected.by_hint",
                        hint=player_hint,
                        player=bus_name,
                    )
                    return bus_name
            logger.warning("media.player_hint.not_found", hint=player_hint)
            return None

        # 2. Reuse last player if healthy
        if self._last_player in candidates:
            logger.debug("media.player_selected.last", player=self._last_player)
            return self._last_player

        # 3. Find playing player
        for bus_name in candidates:
            try:
                props = self._get_properties_proxy(bus_name)
                status = str(props.Get(self.PLAYER_IFACE, "PlaybackStatus"))
                if status == "Playing":
                    self._last_player = bus_name
                    logger.info("media.player_selected.playing", player=bus_name)
                    return bus_name
            except Exception:
                self._mark_failed(bus_name)

        # 4. Fallback to highest priority
        self._last_player = candidates[0]
        logger.info("media.player_selected.fallback", player=self._last_player)
        return self._last_player

    def _extract_media_info(self, bus_name: str) -> MediaInfo:
        """
        Extract and normalize MediaInfo from player metadata.

        Args:
            bus_name: Player D-Bus service name.

        Returns:
            Populated MediaInfo instance.
        """
        props = self._get_properties_proxy(bus_name)
        metadata = self._get_metadata(bus_name)

        # Basic metadata
        title = self._extract_meta_str(metadata, "xesam:title", "Unknown")
        artist = self._extract_meta_artist(metadata)
        album = self._extract_meta_optional_str(metadata, "xesam:album")
        artwork_url = self._extract_meta_optional_str(metadata, "mpris:artUrl")

        # Playback properties
        status_raw = str(self._safe_get_prop(props, "PlaybackStatus", "Stopped"))
        volume = float(self._safe_get_prop(props, "Volume", 1.0))
        shuffle = bool(self._safe_get_prop(props, "Shuffle", False))
        loop_status = str(self._safe_get_prop(props, "LoopStatus", "None"))
        position_us = int(self._safe_get_prop(props, "Position", 0))

        # Capabilities
        can_seek = self._safe_get_bool(props, "CanSeek", default=False)
        can_go_next = self._safe_get_bool(props, "CanGoNext", default=True)
        can_go_previous = self._safe_get_bool(props, "CanGoPrevious", default=True)

        # Length from metadata
        length_us = self._extract_meta_int(metadata, "mpris:length")

        # Map to domain enums
        status = self._PLAYBACK_STATUS_MAP.get(status_raw, PlaybackStatus.STOPPED)
        repeat_mode = self._REPEAT_MODE_MAP.get(loop_status, RepeatMode.NONE)
        shuffle_mode = ShuffleMode.ON if shuffle else ShuffleMode.OFF

        return MediaInfo(
            title=title,
            artist=artist,
            album=album,
            player_name=self._get_display_name(bus_name),
            status=status,
            position_ms=max(0, position_us // 1000),
            length_ms=(length_us // 1000) if length_us else None,
            artwork_url=artwork_url,
            repeat_mode=repeat_mode,
            shuffle_mode=shuffle_mode,
            volume=volume,
            can_seek=can_seek,
            can_go_next=can_go_next,
            can_go_previous=can_go_previous,
        )

    def _get_metadata(self, bus_name: str) -> dict[str, Any]:
        """Fetch raw metadata dictionary from player."""
        props = self._get_properties_proxy(bus_name)
        metadata = props.Get(self.PLAYER_IFACE, "Metadata")
        return dict(metadata) if metadata else {}

    def _get_display_name(self, bus_name: str) -> str:
        """Get human-readable name for player."""
        return PlayerIdentifier.from_bus_name(bus_name).display_name

    async def _seek_relative(self, offset_ms: int, player_name: str | None) -> Result:
        """Execute relative seek operation."""
        logger.info(
            "media.seek_relative.started",
            offset_ms=offset_ms,
            player_name=player_name,
        )

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            player_proxy = self._get_player_proxy(player)
            player_proxy.Seek(int(offset_ms) * 1000)

            direction = "forward" if offset_ms >= 0 else "backward"
            return Result(
                success=True,
                message=f"Track skipped {direction} {abs(offset_ms) // 1000}s",
                data={
                    "player": self._get_display_name(player),
                    "offset_ms": offset_ms,
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, "seek_relative")
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, "seek_relative")

    async def _send_command(
        self,
        method: str,
        success_message: str,
        player_name: str | None,
    ) -> Result:
        """Generic command sender with error handling."""
        logger.info(
            "media.command.started",
            method=method,
            player_name=player_name,
        )

        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            proxy = self._get_player_proxy(player)
            getattr(proxy, method)()

            logger.info(
                "media.command.completed",
                method=method,
                player=self._get_display_name(player),
            )
            return Result(
                success=True,
                message=success_message,
                data={
                    "player": self._get_display_name(player),
                    "method": method,
                },
            )

        except DBusError as exc:
            return self._handle_dbus_error(exc, player, method)
        except Exception as exc:
            return self._handle_unexpected_error(exc, player, method)

    async def _force_previous_track(
        self,
        player: str,
        player_proxy: InterfaceProxy,
    ) -> None:
        """
        Force previous track by seeking to start first.

        This works around MPRIS2 behavior where Previous() restarts
        current track if position > threshold.
        """
        try:
            metadata = self._get_metadata(player)
            track_id = metadata.get("mpris:trackid")
            if track_id:
                player_proxy.SetPosition(track_id, 0)
        except Exception:
            pass  # Best effort, continue to Previous()

    def _player_not_found_result(self, player_name: str | None) -> Result:
        """Generate consistent not-found error result."""
        if player_name:
            logger.warning("media.player_not_found", player_name=player_name)
            return Result(
                success=False,
                message=f"Player not found: {player_name}",
            )
        logger.warning("media.no_active_player")
        return Result(
            success=False,
            message="No active media player available",
        )

    def _handle_dbus_error(self, exc: DBusError, player: str, operation: str) -> Result:
        """Standardized DBus error handling."""
        self._mark_failed(player)
        logger.warning(
            f"media.{operation}.dbus_error",
            error=str(exc),
            player=player,
        )
        return Result(
            success=False,
            message=f"D-Bus error: {exc}",
        )

    def _handle_unexpected_error(
        self,
        exc: Exception,
        player: str,
        operation: str,
    ) -> Result:
        """Standardized unexpected error handling."""
        self._mark_failed(player)
        logger.exception(
            f"media.{operation}.unexpected_error",
            error=str(exc),
            player=player,
        )
        return Result(
            success=False,
            message=str(exc),
        )

    def _mark_failed(self, bus_name: str) -> None:
        """Increment failure counter for circuit-breaker pattern."""
        self._failed_buses[bus_name] += 1
        logger.warning(
            "media.bus_marked_failed",
            bus_name=bus_name,
            failures=self._failed_buses[bus_name],
        )

    # -------------------------------------------------------------------------
    # Metadata Extraction Helpers
    # -------------------------------------------------------------------------

    def _safe_get_prop(
        self, props: InterfaceProxy, prop_name: str, default: Any
    ) -> Any:
        """Safely get and unpack a D-Bus property."""
        try:
            return props.Get(self.PLAYER_IFACE, prop_name).unpack()
        except Exception:
            return default

    def _safe_get_bool(
        self,
        props: InterfaceProxy,
        prop_name: str,
        default: bool,
    ) -> bool:
        """Safely get boolean property with default."""
        try:
            return bool(props.Get(self.PLAYER_IFACE, prop_name).unpack())
        except Exception:
            return default

    def _extract_meta_str(
        self,
        metadata: dict[str, Any],
        key: str,
        default: str = "",
    ) -> str:
        """Extract string metadata, handling list values."""
        value = metadata.get(key, default)
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else default
        return str(value) if value is not None else default

    def _extract_meta_optional_str(
        self,
        metadata: dict[str, Any],
        key: str,
    ) -> str | None:
        """Extract optional string metadata."""
        value = metadata.get(key)
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            if not value:
                return None
            value = value[0]
        text = str(value).strip()
        return text or None

    def _extract_meta_artist(self, metadata: dict[str, Any]) -> str:
        """Extract artist string from potentially list metadata."""
        artists = metadata.get("xesam:artist")
        if isinstance(artists, (list, tuple)) and artists:
            return ", ".join(str(a) for a in artists if a)
        if artists:
            return str(artists)
        return "Unknown"

    def _extract_meta_int(self, metadata: dict[str, Any], key: str) -> int | None:
        """Extract integer metadata safely."""
        value = metadata.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
