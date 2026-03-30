"""Adapter MPRIS2 para control multimedia en Linux."""

from typing import Any

from dasbus.connection import SessionMessageBus
from dasbus.error import DBusError
from gi.repository import GLib  # type: ignore

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

logger = get_logger(layer="infrastructure", component="mpris_adapter")


class MprisMediaAdapter(MediaControlPort):
    """Adapter de control multimedia vía MPRIS2 sobre DBus."""

    PLAYER_PATH = "/org/mpris/MediaPlayer2"
    PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
    PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
    DBUS_IFACE = "org.freedesktop.DBus"
    MPRIS_PREFIX = "org.mpris.MediaPlayer2."

    PLAYER_PRIORITY = {
        "spotify": 100,
        "elisa": 90,
        "vlc": 80,
        "plasma-browser-integration": 70,
        "firefox": 60,
        "brave": 50,
        "chromium": 40,
    }

    def __init__(self, bus: SessionMessageBus | None = None):
        self._bus = bus or SessionMessageBus()
        self._last_player: str | None = None
        self._failed_buses: dict[str, int] = {}

        logger.info("media_adapter.initialized", backend="mpris2", bus_type="session")

    def is_available(self) -> bool:
        try:
            proxy: Any = self._bus.get_proxy(
                service_name=self.DBUS_IFACE,
                object_path="/org/freedesktop/DBus",
                interface_name=self.DBUS_IFACE,
            )
            proxy.ListNames()
            logger.debug("media_adapter.available")
            return True
        except Exception as e:
            logger.warning("media_adapter.unavailable", error=str(e))
            return False

    async def toggle_playback(self, player_name: str | None = None) -> Result:
        return await self._send_command(
            "PlayPause", "⏯️ Reproducción alternada", player_name=player_name
        )

    async def next_track(self, player_name: str | None = None) -> Result:
        return await self._send_command(
            "Next", "⏭️ Siguiente pista", player_name=player_name
        )

    async def previous_track(
        self, force: bool = False, player_name: str | None = None
    ) -> Result:
        logger.info(
            "media_port.previous.started",
            force=force,
            player_name=player_name,
        )
        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            player_proxy = self._get_player_proxy(player)

            if force:
                # Truco práctico: ir al inicio y luego Previous para forzar pista anterior
                try:
                    metadata = self._get_metadata(player)
                    track_id = metadata.get("mpris:trackid")
                    if track_id:
                        player_proxy.SetPosition(track_id, 0)
                except Exception:
                    pass

            player_proxy.Previous()
            logger.info(
                "media_port.previous.completed",
                success=True,
                player=self._get_player_name(player),
                force=force,
            )
            return Result(
                True,
                "⏮️ Pista anterior",
                data={"player": self._get_player_name(player), "force": force},
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.previous.dbus_error",
                error=str(e),
                player=player,
                force=force,
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception("media.previous.error", error=str(e), player=player)
            self._mark_failed(player)
            return Result(False, str(e))

    async def stop(self, player_name: str | None = None) -> Result:
        return await self._send_command(
            "Stop", "⏹️ Reproducción detenida", player_name=player_name
        )

    async def seek(self, position_ms: int, player_name: str | None = None) -> Result:
        """Seek absoluto a `position_ms` milisegundos usando trackid actual."""
        logger.info(
            "media_port.seek_absolute.started",
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
                return Result(False, "El reproductor no expone track id para seek")

            position_us = max(0, int(position_ms) * 1000)
            player_proxy.SetPosition(track_id, position_us)

            mins, secs = divmod(position_ms // 1000, 60)
            logger.info(
                "media_port.seek_absolute.completed",
                success=True,
                player=self._get_player_name(player),
                position_ms=position_ms,
            )
            return Result(
                True,
                f"⏱️ {mins}:{secs:02d}",
                data={
                    "player": self._get_player_name(player),
                    "position_ms": position_ms,
                },
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.seek_absolute.dbus_error",
                error=str(e),
                player=player,
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception("media.seek.error", error=str(e), player=player)
            self._mark_failed(player)
            return Result(False, str(e))

    async def skip_forward(
        self, offset_ms: int = 10000, player_name: str | None = None
    ) -> Result:
        return await self._seek_relative(abs(int(offset_ms)), player_name=player_name)

    async def skip_backward(
        self, offset_ms: int = 10000, player_name: str | None = None
    ) -> Result:
        return await self._seek_relative(-abs(int(offset_ms)), player_name=player_name)

    async def get_repeat_mode(self, player_name: str | None = None) -> RepeatMode:
        player = self._pick_player(player_name)
        if not player:
            return RepeatMode.NONE

        try:
            props = self._get_properties_proxy(player)
            loop_status = props.Get(self.PLAYER_IFACE, "LoopStatus")

            mapping = {
                "None": RepeatMode.NONE,
                "Track": RepeatMode.TRACK,
                "Playlist": RepeatMode.PLAYLIST,
            }
            return mapping.get(str(loop_status), RepeatMode.NONE)
        except Exception as e:
            logger.warning("media.repeat.get_failed", error=str(e), player=player)
            self._mark_failed(player)
            return RepeatMode.NONE

    async def set_repeat_mode(
        self, mode: RepeatMode, player_name: str | None = None
    ) -> Result:
        logger.info(
            "media_port.repeat_set.started",
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
                RepeatMode.TRACK: "🔂 Canción",
                RepeatMode.PLAYLIST: "🔁 Playlist",
            }

            return Result(
                True,
                icons[mode],
                data={"player": self._get_player_name(player), "mode": mode.value},
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.repeat_set.dbus_error",
                error=str(e),
                player=player,
                mode=mode.value,
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception("media.repeat.set_failed", error=str(e), player=player)
            self._mark_failed(player)
            return Result(False, str(e))

    async def cycle_repeat_mode(self, player_name: str | None = None) -> Result:
        current = await self.get_repeat_mode(player_name=player_name)

        next_mode = {
            RepeatMode.NONE: RepeatMode.TRACK,
            RepeatMode.TRACK: RepeatMode.PLAYLIST,
            RepeatMode.PLAYLIST: RepeatMode.NONE,
        }[current]

        return await self.set_repeat_mode(next_mode, player_name=player_name)

    async def toggle_shuffle(self, player_name: str | None = None) -> Result:
        logger.info("media_port.shuffle_toggle.started", player_name=player_name)
        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            props = self._get_properties_proxy(player)
            current = bool(props.Get(self.PLAYER_IFACE, "Shuffle"))
            new_value = not current
            props.Set(self.PLAYER_IFACE, "Shuffle", new_value)

            status = "🔀 ON" if new_value else "➡️ OFF"
            return Result(
                True,
                f"Shuffle {status}",
                data={"player": self._get_player_name(player), "shuffle": new_value},
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.shuffle_toggle.dbus_error", error=str(e), player=player
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception("media.shuffle.toggle_failed", error=str(e), player=player)
            self._mark_failed(player)
            return Result(False, str(e))

    async def set_volume(self, volume: float, player_name: str | None = None) -> Result:
        """Ajusta volumen en el rango 0.0 a 1.0."""
        logger.info(
            "media_port.volume_set.started", volume=volume, player_name=player_name
        )
        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            clamped = max(0.0, min(1.0, float(volume)))
            props = self._get_properties_proxy(player)
            props.Set(self.PLAYER_IFACE, "Volume", GLib.Variant("d", float(clamped)))

            return Result(
                True,
                f"🔊 {int(clamped * 100)}%",
                data={
                    "player": self._get_player_name(player),
                    "volume": clamped,
                    "volume_pct": int(clamped * 100),
                },
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.volume_set.dbus_error", error=str(e), player=player
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception("media.volume.set_failed", error=str(e), player=player)
            self._mark_failed(player)
            return Result(False, str(e))

    async def get_current_track(
        self, player_name: str | None = None
    ) -> MediaInfo | None:
        logger.debug("media_port.current_track.started", player_name=player_name)
        player = self._pick_player(player_name)
        if not player:
            return None

        try:
            props = self._get_properties_proxy(player)
            metadata = self._get_metadata(player)

            title = self._meta_str(metadata, "xesam:title", "Desconocido")
            artist = self._meta_artist(metadata)
            album = self._meta_optional_str(metadata, "xesam:album")
            artwork_url = self._meta_optional_str(metadata, "mpris:artUrl")

            # USAMOS EL NUEVO MÉTODO SEGURO AQUÍ
            status_raw = str(self._safe_get_prop(props, "PlaybackStatus", "Stopped"))
            volume = float(self._safe_get_prop(props, "Volume", 1.0))
            shuffle = bool(self._safe_get_prop(props, "Shuffle", False))
            loop_status = str(self._safe_get_prop(props, "LoopStatus", "None"))
            position_us = int(self._safe_get_prop(props, "Position", 0))

            length_us = self._meta_int(metadata, "mpris:length")
            can_seek = self._safe_get_bool(props, "CanSeek", default=False)
            can_go_next = self._safe_get_bool(props, "CanGoNext", default=True)
            can_go_previous = self._safe_get_bool(props, "CanGoPrevious", default=True)

            status = {
                "Playing": PlaybackStatus.PLAYING,
                "Paused": PlaybackStatus.PAUSED,
                "Stopped": PlaybackStatus.STOPPED,
            }.get(status_raw, PlaybackStatus.STOPPED)

            repeat_mode = {
                "None": RepeatMode.NONE,
                "Track": RepeatMode.TRACK,
                "Playlist": RepeatMode.PLAYLIST,
            }.get(loop_status, RepeatMode.NONE)

            shuffle_mode = ShuffleMode.ON if shuffle else ShuffleMode.OFF

            track = MediaInfo(
                title=title,
                artist=artist,
                album=album,
                player_name=self._get_player_name(player),
                status=status,
                position_ms=max(0, position_us // 1000),
                length_ms=(length_us // 1000) if length_us is not None else None,
                artwork_url=artwork_url,
                repeat_mode=repeat_mode,
                shuffle_mode=shuffle_mode,
                volume=volume,
                can_seek=can_seek,
                can_go_next=can_go_next,
                can_go_previous=can_go_previous,
            )
            logger.debug(
                "media_port.current_track.completed",
                player=track.player_name,
                status=track.status.value,
            )
            return track
        except Exception as e:
            logger.warning("media.current_track.failed", error=str(e), player=player)
            self._mark_failed(player)
            return None

    async def list_players(self) -> list[MediaPlayer]:
        players: list[MediaPlayer] = []
        logger.info("media_port.list_players.started")

        bus_names = self._list_mpris_buses()
        if not bus_names:
            self._last_player = None
            self._failed_buses.clear()
            logger.info("media_port.list_players.empty")
            return players

        # Limpieza: si reaparece un bus, sale de la cuarentena
        alive = set(bus_names)
        self._failed_buses = {
            name: failures
            for name, failures in self._failed_buses.items()
            if name in alive
        }

        current = self._pick_player()

        for bus_name in bus_names:
            try:
                proxy = self._get_properties_proxy(bus_name)
                status_raw = str(proxy.Get(self.PLAYER_IFACE, "PlaybackStatus"))
                is_active = bus_name == current or status_raw == "Playing"

                players.append(
                    MediaPlayer(
                        bus_name=bus_name,
                        name=self._get_player_name(bus_name),
                        is_active=is_active,
                    )
                )
            except Exception as e:
                logger.debug("media.player.skip", player=bus_name, error=str(e))
                self._mark_failed(bus_name)

        logger.info("media_port.list_players.completed", count=len(players))
        return players

    async def _seek_relative(
        self, offset_ms: int, player_name: str | None = None
    ) -> Result:
        logger.info(
            "media_port.seek_relative.started",
            offset_ms=offset_ms,
            player_name=player_name,
        )
        player = self._pick_player(player_name)
        if not player:
            return self._player_not_found_result(player_name)

        try:
            player_proxy = self._get_player_proxy(player)
            player_proxy.Seek(int(offset_ms) * 1000)

            direction = "adelantado" if offset_ms >= 0 else "retrocedido"
            return Result(
                True,
                f"Track {direction} {abs(offset_ms) // 1000}s",
                data={"player": self._get_player_name(player), "offset_ms": offset_ms},
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.seek_relative.dbus_error", error=str(e), player=player
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception("media.seek.relative_failed", error=str(e), player=player)
            self._mark_failed(player)
            return Result(False, str(e))

    async def _send_command(
        self,
        method: str,
        success_message: str,
        player_name: str | None = None,
    ) -> Result:
        logger.info(
            "media_port.command.started",
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
                "media_port.command.completed",
                method=method,
                success=True,
                player=self._get_player_name(player),
            )
            return Result(
                True,
                success_message,
                data={"player": self._get_player_name(player), "method": method},
            )
        except DBusError as e:
            self._mark_failed(player)
            logger.warning(
                "media_port.command.dbus_error",
                error=str(e),
                player=player,
                method=method,
            )
            return Result(False, f"Error DBus: {e}")
        except Exception as e:
            logger.exception(
                "media.command.failed", player=player, method=method, error=str(e)
            )
            self._mark_failed(player)
            return Result(False, str(e))

    def _get_priority(self, bus_name: str) -> int:
        name = bus_name.replace(self.MPRIS_PREFIX, "").lower()
        base_name = name.split(".instance")[0]
        return self.PLAYER_PRIORITY.get(base_name, 10)

    def _list_mpris_buses(self) -> list[str]:
        try:
            proxy: Any = self._bus.get_proxy(
                service_name=self.DBUS_IFACE,
                object_path="/org/freedesktop/DBus",
                interface_name=self.DBUS_IFACE,
            )
            names = proxy.ListNames()
            mpris_buses = [
                name for name in names if str(name).startswith(self.MPRIS_PREFIX)
            ]
            # Ordenar usando la prioridad definida
            logger.debug("media_port.buses_discovered", count=len(mpris_buses))
            return sorted(mpris_buses, key=self._get_priority, reverse=True)
        except Exception as e:
            logger.warning("media.list_buses.failed", error=str(e))
            return []

    def _pick_player(self, player_hint: str | None = None) -> str | None:
        buses = self._list_mpris_buses()
        if not buses:
            self._last_player = None
            return None

        healthy = [b for b in buses if b not in self._failed_buses]
        candidates = healthy or buses

        if player_hint:
            hint_lower = player_hint.lower()
            for bus_name in candidates:
                player_name = self._get_player_name(bus_name).lower()
                bus_key = bus_name.replace(self.MPRIS_PREFIX, "").lower()
                if hint_lower in player_name or hint_lower in bus_key:
                    self._last_player = bus_name
                    logger.info(
                        "media_port.player_selected_by_hint",
                        player_hint=player_hint,
                        player=bus_name,
                    )
                    return bus_name
            logger.warning("media_port.player_hint_not_found", player_hint=player_hint)
            return None

        if self._last_player in candidates:
            logger.debug("media_port.player_selected_last", player=self._last_player)
            return self._last_player

        # Preferir un player reproduciendo, respetando el orden de prioridad
        for bus_name in candidates:
            try:
                props = self._get_properties_proxy(bus_name)
                status_raw = str(props.Get(self.PLAYER_IFACE, "PlaybackStatus"))
                if status_raw == "Playing":
                    self._last_player = bus_name
                    logger.info("media_port.player_selected_playing", player=bus_name)
                    return bus_name
            except Exception:
                self._mark_failed(bus_name)

        self._last_player = candidates[0]
        logger.info("media_port.player_selected_fallback", player=self._last_player)
        return self._last_player

    def _player_not_found_result(self, player_name: str | None) -> Result:
        if player_name:
            logger.warning("media_port.player_not_found", player_name=player_name)
            return Result(False, f"No se encontró el reproductor: {player_name}")
        logger.warning("media_port.no_active_player")
        return Result(False, "No hay reproductor multimedia activo")

    def _get_player_proxy(self, bus_name: str) -> Any:
        return self._bus.get_proxy(
            service_name=bus_name,
            object_path=self.PLAYER_PATH,
            interface_name=self.PLAYER_IFACE,
        )

    def _get_properties_proxy(self, bus_name: str) -> Any:
        return self._bus.get_proxy(
            service_name=bus_name,
            object_path=self.PLAYER_PATH,
            interface_name=self.PROPERTIES_IFACE,
        )

    def _get_metadata(self, bus_name: str) -> dict[str, Any]:
        props = self._get_properties_proxy(bus_name)
        metadata = props.Get(self.PLAYER_IFACE, "Metadata")
        return dict(metadata) if metadata else {}

    def _get_player_name(self, bus_name: str) -> str:
        base = bus_name.replace(self.MPRIS_PREFIX, "").split(".instance")[0]
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

    def _safe_get_bool(self, props, prop_name: str, default: bool) -> bool:
        try:
            return bool(props.Get(self.PLAYER_IFACE, prop_name).unpack())
        except Exception:
            return default

    def _safe_get_prop(self, props, prop_name: str, default: Any) -> Any:
        """Intenta obtener una propiedad de DBus y la desempaqueta de forma segura."""
        try:
            return props.Get(self.PLAYER_IFACE, prop_name).unpack()
        except Exception:
            return default

    def _meta_str(self, metadata: dict[str, Any], key: str, default: str = "") -> str:
        value = metadata.get(key, default)
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else default
        return str(value) if value is not None else default

    def _meta_optional_str(self, metadata: dict[str, Any], key: str) -> str | None:
        value = metadata.get(key)
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            if not value:
                return None
            value = value[0]
        text = str(value).strip()
        return text or None

    def _meta_artist(self, metadata: dict[str, Any]) -> str:
        artists = metadata.get("xesam:artist")
        if isinstance(artists, (list, tuple)) and artists:
            return ", ".join(str(a) for a in artists if a)
        if artists:
            return str(artists)
        return "Desconocido"

    def _meta_int(self, metadata: dict[str, Any], key: str) -> int | None:
        value = metadata.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _mark_failed(self, bus_name: str) -> None:
        self._failed_buses[bus_name] = self._failed_buses.get(bus_name, 0) + 1
        logger.warning(
            "media_port.bus_marked_failed",
            bus_name=bus_name,
            failures=self._failed_buses[bus_name],
        )
