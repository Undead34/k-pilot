"""Tools multimedia modernas y parametrizables."""

from typing import Literal, Optional

from pydantic_ai import RunContext

from k_pilot.application.deps import AppDeps
from k_pilot.domain.models import RepeatMode


async def control_media(
    ctx: RunContext[AppDeps],
    action: Literal["play", "pause", "toggle", "next", "previous", "stop"],
    force_previous: bool = False,
) -> str:
    """
    Control básico: play, pause, toggle, next, previous, stop.

    Args:
        force_previous: Solo para action='previous'.
                       True = fuerza ir a canción anterior (doble skip si es necesario).
                       False = comportamiento normal (puede reiniciar canción si >3s).
    """
    port = ctx.deps.media_port

    if action in ("play", "pause", "toggle"):
        result = await port.play_pause()
        icons = {"play": "▶️", "pause": "⏸️", "toggle": "⏯️"}
        return (
            f"{icons[action]} {result.message}"
            if result.success
            else f"✗ {result.message}"
        )

    elif action == "next":
        result = await port.next_track()
    elif action == "previous":
        result = await port.previous_track(force=force_previous)
        return result.message
    elif action == "stop":
        result = await port.stop()
    else:
        return "❌ Acción inválida"

    return f"{'✓' if result.success else '✗'} {result.message}"


async def get_now_playing(ctx: RunContext[AppDeps]) -> str:
    """Info enriquecida de la canción actual."""
    track = await ctx.deps.media_port.get_current_track()
    if not track:
        return "🎵 No hay nada reproduciéndose"

    status_icons = {"PLAYING": "▶️", "PAUSED": "⏸️", "STOPPED": "⏹️"}
    icon = status_icons.get(track.status.value, "🎵")

    repeat_icon = (
        ""
        if track.repeat_mode == RepeatMode.NONE
        else (" 🔂" if track.repeat_mode == RepeatMode.TRACK else " 🔁")
    )
    shuffle_icon = " 🔀" if track.shuffle_mode.value == "ON" else ""

    lines = [
        f"{icon} **{track.title}**{repeat_icon}{shuffle_icon}",
        f"🎤 {track.artist}",
    ]
    if track.album:
        lines.append(f"💿 {track.album}")
    lines.append(f"🖥️ {track.player_name} | 🔊 {int(track.volume * 100)}%")

    if track.position_ms and track.length_ms:
        pct = track.position_ms / track.length_ms
        bar = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
        pm, ps = divmod(track.position_ms // 1000, 60)
        lm, ls = divmod(track.length_ms // 1000, 60)
        lines.append(f"⏱️ [{bar}] {pm}:{ps:02d}/{lm}:{ls:02d}")

    return "\n".join(lines)


async def list_media_players(ctx: RunContext[AppDeps]) -> str:
    players = await ctx.deps.media_port.list_players()
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
) -> str:
    """
    Navegación temporal.

    Args:
        seconds: Segundos a saltar o posición absoluta
        relative: True = salta +/-seconds, False = va a seconds exacto
        direction: Si relative=True, 'forward' o 'backward'
    """
    port = ctx.deps.media_port

    if relative:
        offset = seconds * 1000
        if direction == "forward":
            result = await port.skip_forward(offset)
            icon = "⏩"
        else:
            result = await port.skip_backward(offset)
            icon = "⏪"
        text = f"{icon} {seconds}s {direction}"
    else:
        result = await port.seek(seconds * 1000)
        mins, secs = divmod(seconds, 60)
        text = f"⏱️ Posición {mins}:{secs:02d}"

    return (
        f"{'✓' if result.success else '✗'} {text}"
        if result.success
        else f"✗ {result.message}"
    )


async def set_repeat(
    ctx: RunContext[AppDeps],
    mode: Literal["off", "track", "playlist", "cycle", "status"] = "status",
) -> str:
    """
    Control de repetición.

    Args:
        mode: 'off', 'track' (canción), 'playlist', 'cycle' (rotar), 'status' (ver actual)
    """
    port = ctx.deps.media_port

    if mode == "status":
        current = await port.get_repeat_mode()
        icons = {
            RepeatMode.NONE: "🔁 Desactivado",
            RepeatMode.TRACK: "🔂 Repetir canción",
            RepeatMode.PLAYLIST: "🔁 Repetir playlist",
        }
        return f"Modo: {icons[current]}"

    if mode == "cycle":
        result = await port.cycle_repeat_mode()
    else:
        mode_map = {
            "off": RepeatMode.NONE,
            "track": RepeatMode.TRACK,
            "playlist": RepeatMode.PLAYLIST,
        }
        result = await port.set_repeat_mode(mode_map[mode])

    return f"{'✓' if result.success else '✗'} {result.message}"


async def toggle_shuffle(ctx: RunContext[AppDeps]) -> str:
    """Activa/desactiva aleatorio."""
    result = await ctx.deps.media_port.toggle_shuffle()
    return f"{'✓' if result.success else '✗'} {result.message}"


async def set_volume(
    ctx: RunContext[AppDeps],
    level: float,
    relative: bool = False,
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

    Returns:
        Mensaje de confirmación formateado con icono y porcentaje final,
        o mensaje de error si no hay reproductor o no soporta control de volumen.

    Examples:
        >>> await set_volume(ctx, 0.75)           # "Volumen al 75%"
        >>> await set_volume(ctx, 0.0)            # "Mute / Silencio"
        >>> await set_volume(ctx, 1.2)            # Clamp a 100%
        >>> await set_volume(ctx, 0.1, True)      # Subir 10% desde el actual
        >>> await set_volume(ctx, -0.15, True)    # Bajar 15% desde el actual
    """
    port = ctx.deps.media_port

    # Modo relativo: calcular volumen objetivo basado en el actual
    if relative:
        track = await port.get_current_track()
        if not track:
            return "❌ No hay reproductor multimedia activo"

        current_vol = track.volume  # Float 0.0-1.0 del MPRIS
        target_vol = current_vol + level

        # Determinar dirección para el mensaje
        direction = "subido" if level > 0 else "bajado"
        delta_pct = abs(int(level * 100))

        result = await port.set_volume(target_vol)
        if result.success:
            return f"🔊 Volumen {direction} un {delta_pct}% (ahora al {result.data.get('volume_pct', '??')}%)"
        return f"❌ No se pudo ajustar: {result.message}"

    # Modo absoluto: establecer volumen directamente
    else:
        # Validar y convertir porcentaje si viene como entero (ej: 75 -> 0.75)
        if level > 1.0:
            level = level / 100.0

        result = await port.set_volume(level)

        if result.success:
            pct = int(level * 100)
            if pct == 0:
                return "🔇 Volumen silenciado (0%)"
            elif pct == 100:
                return "🔊 Volumen al máximo (100%)"
            else:
                bar = "█" * (pct // 10) + "░" * (10 - (pct // 10))
                return f"🔊 Volumen ajustado [{bar}] {pct}%"

        return f"❌ Error: {result.message}"
