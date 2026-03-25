"""Adapter MPRIS2 con API moderna y parametrizable."""

import asyncio
from typing import Optional

import structlog
from pydbus import SessionBus

from k_pilot.domain.models import (
    MediaInfo,
    MediaPlayer,
    PlaybackStatus,
    RepeatMode,
    Result,
    ShuffleMode,
)
from k_pilot.domain.ports import MediaControlPort

logger = structlog.get_logger()


class MprisMediaAdapter(MediaControlPort):
    MPRIS_PREFIX = "org.mpris.MediaPlayer2."
    PLAYER_PATH = "/org/mpris/MediaPlayer2"
    INTERFACE = "org.mpris.MediaPlayer2.Player"

    PRIORITY = {
        "spotify": 100,
        "elisa": 90,
        "vlc": 80,
        "plasma-browser-integration": 70,
        "firefox": 60,
        "brave": 50,
        "chromium": 40,
    }

    def __init__(self, bus: SessionBus):
        self._bus = bus
        self._dbus_proxy = bus.get("org.freedesktop.DBus", "/org/freedesktop/DBus")
        self._failed_buses = set()
        self._last_player: Optional[str] = None

    def is_available(self) -> bool:
        try:
            return len(self._list_mpris_names()) > 0
        except Exception:
            return False

    def _list_mpris_names(self) -> list[str]:
        try:
            names = self._dbus_proxy.ListNames()
            mpris = [n for n in names if n.startswith(self.MPRIS_PREFIX)]
            return sorted(mpris, key=self._get_priority, reverse=True)
        except Exception as e:
            logger.error("mpris.list_names_failed", error=str(e))
            return []

    def _get_priority(self, bus_name: str) -> int:
        name = bus_name.replace(self.MPRIS_PREFIX, "").lower()
        base_name = name.split(".instance")[0]
        return self.PRIORITY.get(base_name, 10)

    def _get_player_proxy(self, bus_name: Optional[str] = None):
        if bus_name and bus_name not in self._failed_buses:
            try:
                proxy = self._bus.get(bus_name, self.PLAYER_PATH)
                _ = proxy.PlaybackStatus
                self._last_player = bus_name
                return proxy
            except Exception:
                self._failed_buses.add(bus_name)
                if bus_name == self._last_player:
                    self._last_player = None

        if self._last_player:
            return self._get_player_proxy(self._last_player)

        for name in self._list_mpris_names():
            if name in self._failed_buses:
                continue
            proxy = self._get_player_proxy(name)
            if proxy:
                return proxy
        return None

    def _get_player_name(self, bus_name: str) -> str:
        name = bus_name.replace(self.MPRIS_PREFIX, "")
        base = name.split(".instance")[0]
        mapping = {
            "spotify": "Spotify",
            "elisa": "Elisa",
            "vlc": "VLC",
            "firefox": "Firefox",
            "brave": "Brave",
            "chromium": "Chromium",
            "plasma-browser-integration": "Navegador Web",
        }
        return mapping.get(base.lower(), base.capitalize())

    async def list_players(self) -> list[MediaPlayer]:
        players = []
        for bus_name in self._list_mpris_names():
            proxy = self._get_player_proxy(bus_name)
            if proxy:
                players.append(
                    MediaPlayer(
                        bus_name=bus_name,
                        name=self._get_player_name(bus_name),
                        is_active=True,
                    )
                )
        return players

    async def get_current_track(self) -> Optional[MediaInfo]:
        proxy = self._get_player_proxy()
        if not proxy:
            return None

        try:
            metadata = proxy.Metadata
            status_str = proxy.PlaybackStatus

            # Propiedades opcionales MPRIS
            try:
                loop_status = getattr(proxy, "LoopStatus", "None")
                shuffle = getattr(proxy, "Shuffle", False)
                volume = getattr(proxy, "Volume", 1.0)
                can_seek = getattr(proxy, "CanSeek", False)
                can_next = getattr(proxy, "CanGoNext", True)
                can_prev = getattr(proxy, "CanGoPrevious", True)
            except Exception:
                loop_status, shuffle, volume = "None", False, 1.0
                can_seek, can_next, can_prev = False, True, True

            status_map = {
                "Playing": PlaybackStatus.PLAYING,
                "Paused": PlaybackStatus.PAUSED,
                "Stopped": PlaybackStatus.STOPPED,
            }

            repeat_map = {
                "None": RepeatMode.NONE,
                "Track": RepeatMode.TRACK,
                "Playlist": RepeatMode.PLAYLIST,
            }

            def get_meta(key: str, default: str = "") -> str:
                value = metadata.get(key, "")
                if isinstance(value, list):
                    return value[0] if value else default
                return value or default

            title = get_meta("xesam:title", "Desconocido")
            artist = get_meta("xesam:artist", "Desconocido")
            album = get_meta("xesam:album", "")
            artwork = get_meta("mpris:artUrl", None)

            length_us = metadata.get("mpris:length", 0)
            length_ms = length_us // 1000 if length_us else None

            try:
                position_us = proxy.Position
                position_ms = position_us // 1000 if position_us else None
            except Exception:
                position_ms = None

            return MediaInfo(
                title=title,
                artist=artist,
                album=album,
                player_name=self._get_player_name(self._last_player or ""),
                status=status_map.get(status_str, PlaybackStatus.STOPPED),
                position_ms=position_ms,
                length_ms=length_ms,
                artwork_url=artwork,
                repeat_mode=repeat_map.get(loop_status, RepeatMode.NONE),
                shuffle_mode=ShuffleMode.ON if shuffle else ShuffleMode.OFF,
                volume=max(0.0, min(1.0, volume)),
                can_seek=can_seek,
                can_go_next=can_next,
                can_go_previous=can_prev,
            )

        except Exception as e:
            logger.error("mpris.metadata_failed", error=str(e))
            return None

    async def play_pause(self) -> Result:
        return await self._send_command("PlayPause", "⏯️ Play/Pause")

    async def next_track(self) -> Result:
        return await self._send_command("Next", "⏭️ Siguiente")

    async def previous_track(self, force: bool = False) -> Result:
        """
        force=True: Fuerza ir a anterior (doble skip en browsers si >3s)
        force=False: Comportamiento estándar MPRIS
        """
        if not force:
            return await self._send_command("Previous", "⏮️ Anterior")

        # Lógica smart/force
        track = await self.get_current_track()
        if not track:
            return Result(False, "No hay reproductor activo")

        is_browser = track.player_name in [
            "Brave",
            "Firefox",
            "Chromium",
            "Navegador Web",
        ]
        needs_double = is_browser and track.position_ms and track.position_ms > 3000

        try:
            proxy = self._get_player_proxy()
            if not proxy:
                return Result(False, "No se pudo conectar")

            proxy.Previous()

            if needs_double:
                await asyncio.sleep(0.3)
                proxy.Previous()
                msg = "⏮️ Anterior (doble skip)"
            else:
                msg = "⏮️ Anterior"

            return Result(True, msg, data={"player": track.player_name})
        except Exception as e:
            return Result(False, f"Error: {e}")

    async def stop(self) -> Result:
        return await self._send_command("Stop", "⏹️ Detenido")

    async def seek(self, position_ms: int) -> Result:
        try:
            proxy = self._get_player_proxy()
            if not proxy:
                return Result(False, "No hay reproductor")

            proxy.SetPosition(self.PLAYER_PATH, position_ms * 1000)
            mins, secs = divmod(position_ms // 1000, 60)
            return Result(True, f"⏱️ {mins}:{secs:02d}")
        except Exception as e:
            return Result(False, f"Seek error: {e}")

    async def skip_forward(self, offset_ms: int = 10000) -> Result:
        track = await self.get_current_track()
        if not track or track.position_ms is None:
            return Result(False, "Posición desconocida")
        return await self.seek(track.position_ms + offset_ms)

    async def skip_backward(self, offset_ms: int = 10000) -> Result:
        track = await self.get_current_track()
        if not track or track.position_ms is None:
            return Result(False, "Posición desconocida")
        return await self.seek(max(0, track.position_ms - offset_ms))

    async def get_repeat_mode(self) -> RepeatMode:
        try:
            proxy = self._get_player_proxy()
            if not proxy:
                return RepeatMode.NONE
            status = getattr(proxy, "LoopStatus", "None")
            mapping = {
                "None": RepeatMode.NONE,
                "Track": RepeatMode.TRACK,
                "Playlist": RepeatMode.PLAYLIST,
            }
            return mapping.get(status, RepeatMode.NONE)
        except Exception:
            return RepeatMode.NONE

    async def set_repeat_mode(self, mode: RepeatMode) -> Result:
        try:
            proxy = self._get_player_proxy()
            if not proxy:
                return Result(False, "No hay reproductor")
            if not hasattr(proxy, "LoopStatus"):
                return Result(False, "No soporta repetición")

            proxy.LoopStatus = mode.value
            icons = {
                RepeatMode.NONE: "🔁 Off",
                RepeatMode.TRACK: "🔂 Canción",
                RepeatMode.PLAYLIST: "🔁 Playlist",
            }
            return Result(True, icons[mode])
        except Exception as e:
            return Result(False, f"Error: {e}")

    async def cycle_repeat_mode(self) -> Result:
        current = await self.get_repeat_mode()
        cycle = {
            RepeatMode.NONE: RepeatMode.TRACK,
            RepeatMode.TRACK: RepeatMode.PLAYLIST,
            RepeatMode.PLAYLIST: RepeatMode.NONE,
        }
        return await self.set_repeat_mode(cycle[current])

    async def toggle_shuffle(self) -> Result:
        try:
            proxy = self._get_player_proxy()
            if not proxy:
                return Result(False, "No hay reproductor")
            if not hasattr(proxy, "Shuffle"):
                return Result(False, "No soporta shuffle")

            current = getattr(proxy, "Shuffle", False)
            proxy.Shuffle = not current
            status = "🔀 ON" if not current else "➡️ OFF"
            return Result(True, f"Shuffle {status}")
        except Exception as e:
            return Result(False, f"Error: {e}")

    async def set_volume(self, volume: float) -> Result:
        """volume: 0.0 a 1.0 (estándar MPRIS2)"""
        try:
            proxy = self._get_player_proxy()
            if not proxy:
                return Result(False, "No hay reproductor")

            # MPRIS usa float 0.0-1.0, no porcentajes
            clamped = max(0.0, min(1.0, volume))  # ← Protección contra 120%
            proxy.Volume = clamped  # ← Propiedad D-Bus nativa

            return Result(True, f"🔊 {int(clamped * 100)}%")
        except Exception as e:
            return Result(False, f"Error: {e}")

    async def _send_command(self, method: str, success_msg: str) -> Result:
        proxy = self._get_player_proxy()
        if not proxy:
            return Result(False, "No hay reproductores activos")
        try:
            getattr(proxy, method)()
            return Result(True, success_msg)
        except Exception as e:
            return Result(False, str(e))
