"""Tools multimedia modernas y parametrizables."""

from typing import Literal

import structlog
from pydantic_ai import RunContext

from k_pilot.application.deps import AppDeps
from k_pilot.domain.models import PlaybackStatus, RepeatMode, ShuffleMode

logger = structlog.get_logger("k-pilot.media_tools")


async def control_media(
    ctx: RunContext[AppDeps],
    action: Literal["play", "pause", "toggle", "next", "previous", "stop"],
    player_name: str | None = None,
    force_previous: bool = False,
) -> str:
    """
    Control básico: play, pause, toggle, next, previous, stop.

    Args:
        player_name: Reproductor específico a controlar (ej. "vlc", "spotify", "brave").
                     Si se omite, se usa el que esté sonando o el prioritario.
        force_previous: Solo para action='previous'.
                        True = fuerza ir a canción anterior (doble skip si es necesario).
                        False = comportamiento normal (puede reiniciar canción si >3s).
    """
    port = ctx.deps.media_port
    logger.info(
        "use_case.media.control.started",
        action=action,
        player_name=player_name,
        force_previous=force_previous,
    )

    if action in ("play", "pause", "toggle"):
        result = await port.toggle_playback(player_name=player_name)
        icons = {"play": "▶️", "pause": "⏸️", "toggle": "⏯️"}
        return (
            f"{icons[action]} {result.message}"
            if result.success
            else f"✗ {result.message}"
        )

    elif action == "next":
        result = await port.next_track(player_name=player_name)
    elif action == "previous":
        result = await port.previous_track(
            force=force_previous, player_name=player_name
        )
        logger.info(
            "use_case.media.control.completed",
            action=action,
            player_name=player_name,
            success=result.success,
        )
        return result.message
    elif action == "stop":
        result = await port.stop(player_name=player_name)
    else:
        return "❌ Acción inválida"

    logger.info(
        "use_case.media.control.completed",
        action=action,
        player_name=player_name,
        success=result.success,
    )
    return f"{'✓' if result.success else '✗'} {result.message}"


async def get_now_playing(ctx: RunContext[AppDeps]) -> str:
    """Info enriquecida de todos los reproductores multimedia disponibles."""
    port = ctx.deps.media_port
    logger.info("use_case.media.snapshot.started")
    players = await port.list_players()
    if not players:
        logger.info("use_case.media.snapshot.empty")
        return "🎵 No hay reproductores multimedia disponibles"

    status_icons = {
        PlaybackStatus.PLAYING: "▶️",
        PlaybackStatus.PAUSED: "⏸️",
        PlaybackStatus.STOPPED: "⏹️",
    }

    lines = ["🎵 Estado multimedia del sistema:"]

    for player in players:
        track = await port.get_current_track(player_name=player.name)
        if not track:
            logger.warning(
                "use_case.media.snapshot.player_missing_track",
                player_name=player.name,
            )
            continue

        icon = status_icons.get(track.status, "🎵")
        repeat_icon = (
            ""
            if track.repeat_mode == RepeatMode.NONE
            else (" 🔂" if track.repeat_mode == RepeatMode.TRACK else " 🔁")
        )
        shuffle_icon = " 🔀" if track.shuffle_mode == ShuffleMode.ON else ""
        active_marker = " 🟢" if player.is_active else ""

        lines.append(
            f"\n🖥️ {track.player_name}{active_marker} | 🔊 {int(track.volume * 100)}%"
        )
        lines.append(f"{icon} **{track.title}**{repeat_icon}{shuffle_icon}")
        lines.append(f"🎤 {track.artist}")

        if track.album:
            lines.append(f"💿 {track.album}")

        if track.position_ms is not None and track.length_ms:
            pct = min(max(track.position_ms / track.length_ms, 0), 1)
            bar = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
            pm, ps = divmod(track.position_ms // 1000, 60)
            lm, ls = divmod(track.length_ms // 1000, 60)
            lines.append(f"⏱️ [{bar}] {pm}:{ps:02d}/{lm}:{ls:02d}")

    logger.info("use_case.media.snapshot.completed", players=len(players))
    return (
        "\n".join(lines)
        if len(lines) > 1
        else "🎵 No hay información multimedia disponible"
    )


async def list_media_players(ctx: RunContext[AppDeps]) -> str:
    players = await ctx.deps.media_port.list_players()
    logger.info("use_case.media.players_listed", count=len(players))
    if not players:
        return "No hay reproductores activos"
    lines = ["🎵 Reproductores:"]
    for i, p in enumerate(players, 1):
        lines.append(f"{'🟢' if p.is_active else '⚪'} {i}. {p.name}")
    return "\n".join(lines)


async def seek(
    ctx: RunContext[AppDeps],
    seconds: int,
    relative: bool = True,
    direction: Literal["forward", "backward"] = "forward",
    player_name: str | None = None,
) -> str:
    """
    Navegación temporal.

    Args:
        seconds: Segundos a saltar o posición absoluta
        relative: True = salta +/-seconds, False = va a seconds exacto
        direction: Si relative=True, 'forward' o 'backward'
        player_name: Reproductor específico a controlar.
    """
    port = ctx.deps.media_port
    logger.info(
        "use_case.media.seek.started",
        seconds=seconds,
        relative=relative,
        direction=direction,
        player_name=player_name,
    )

    if relative:
        offset = seconds * 1000
        if direction == "forward":
            result = await port.skip_forward(offset, player_name=player_name)
            icon = "⏩"
        else:
            result = await port.skip_backward(offset, player_name=player_name)
            icon = "⏪"
        text = f"{icon} {seconds}s {direction}"
    else:
        result = await port.seek(seconds * 1000, player_name=player_name)
        mins, secs = divmod(seconds, 60)
        text = f"⏱️ Posición {mins}:{secs:02d}"

    logger.info(
        "use_case.media.seek.completed",
        player_name=player_name,
        relative=relative,
        success=result.success,
    )
    return (
        f"{'✓' if result.success else '✗'} {text}"
        if result.success
        else f"✗ {result.message}"
    )


async def set_repeat(
    ctx: RunContext[AppDeps],
    mode: Literal["off", "track", "playlist", "cycle", "status"] = "status",
    player_name: str | None = None,
) -> str:
    """
    Control de repetición.

    Args:
        mode: 'off', 'track' (canción), 'playlist', 'cycle' (rotar), 'status' (ver actual)
        player_name: Reproductor específico a controlar.
    """
    port = ctx.deps.media_port
    logger.info("use_case.media.repeat.started", mode=mode, player_name=player_name)

    if mode == "status":
        current = await port.get_repeat_mode(player_name=player_name)
        icons = {
            RepeatMode.NONE: "🔁 Desactivado",
            RepeatMode.TRACK: "🔂 Repetir canción",
            RepeatMode.PLAYLIST: "🔁 Repetir playlist",
        }
        logger.info(
            "use_case.media.repeat.completed",
            mode=mode,
            player_name=player_name,
            success=True,
        )
        return f"Modo: {icons[current]}"

    if mode == "cycle":
        result = await port.cycle_repeat_mode(player_name=player_name)
    else:
        mode_map = {
            "off": RepeatMode.NONE,
            "track": RepeatMode.TRACK,
            "playlist": RepeatMode.PLAYLIST,
        }
        result = await port.set_repeat_mode(mode_map[mode], player_name=player_name)

    logger.info(
        "use_case.media.repeat.completed",
        mode=mode,
        player_name=player_name,
        success=result.success,
    )
    return f"{'✓' if result.success else '✗'} {result.message}"


async def toggle_shuffle(
    ctx: RunContext[AppDeps], player_name: str | None = None
) -> str:
    """Activa/desactiva aleatorio."""
    logger.info("use_case.media.shuffle.started", player_name=player_name)
    result = await ctx.deps.media_port.toggle_shuffle(player_name=player_name)
    logger.info(
        "use_case.media.shuffle.completed",
        player_name=player_name,
        success=result.success,
    )
    return f"{'✓' if result.success else '✗'} {result.message}"


async def set_volume(
    ctx: RunContext[AppDeps],
    level: float,
    relative: bool = False,
    player_name: str | None = None,
) -> str:
    """
    Ajusta el volumen del reproductor multimedia activo.

    Maneja tanto valores absolutos ("al 75%") como relativos ("subir 10%").
    El volumen se clamp automáticamente entre 0% (mute) y 100% (máximo)
    según el estándar MPRIS2.

    Args:
        level: Nivel de volumen objetivo.
               - Si relative=False: Valor absoluto entre 0.0 y 1.0
                 (donde 0.0 = 0%, 1.0 = 100%).
               - Si relative=True: Delta a aplicar al volumen actual
                 (ej: +0.1 para subir 10%, -0.2 para bajar 20%).
        relative: Modo de operación.
                  - False (default): Establece volumen absoluto.
                  - True: Ajusta relativamente al volumen actual (incremento/decremento).
        player_name: Reproductor específico a controlar.
    """
    port = ctx.deps.media_port
    logger.info(
        "use_case.media.volume.started",
        level=level,
        relative=relative,
        player_name=player_name,
    )

    # Modo relativo: calcular volumen objetivo basado en el actual
    if relative:
        track = await port.get_current_track(player_name=player_name)
        if not track:
            return (
                f"❌ No se encontró el reproductor: {player_name}"
                if player_name
                else "❌ No hay reproductor multimedia activo"
            )

        current_vol = track.volume  # Float 0.0-1.0 del MPRIS
        target_vol = current_vol + level

        # Determinar dirección para el mensaje
        direction = "subido" if level > 0 else "bajado"
        delta_pct = abs(int(level * 100))

        result = await port.set_volume(target_vol, player_name=player_name)

        if result.success:
            vol = result.data.get("volume_pct", "??") if result.data else "??"
            logger.info(
                "use_case.media.volume.completed",
                relative=relative,
                player_name=player_name,
                success=True,
                volume_pct=vol,
            )
            return f"🔊 Volumen {direction} un {delta_pct}% (ahora al {vol}%)"

        logger.info(
            "use_case.media.volume.completed",
            relative=relative,
            player_name=player_name,
            success=False,
        )
        return f"❌ No se pudo ajustar: {result.message}"

    # Modo absoluto: establecer volumen directamente
    else:
        # Validar y convertir porcentaje si viene como entero (ej: 75 -> 0.75)
        if level > 1.0:
            level = level / 100.0

        result = await port.set_volume(level, player_name=player_name)

        if result.success:
            pct = int(level * 100)
            logger.info(
                "use_case.media.volume.completed",
                relative=relative,
                player_name=player_name,
                success=True,
                volume_pct=pct,
            )
            if pct == 0:
                return "🔇 Volumen silenciado (0%)"
            elif pct == 100:
                return "🔊 Volumen al máximo (100%)"
            else:
                bar = "█" * (pct // 10) + "░" * (10 - (pct // 10))
                return f"🔊 Volumen ajustado [{bar}] {pct}%"

        logger.info(
            "use_case.media.volume.completed",
            relative=relative,
            player_name=player_name,
            success=False,
        )
        return f"❌ Error: {result.message}"
