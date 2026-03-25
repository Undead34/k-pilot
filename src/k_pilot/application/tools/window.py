from pydantic_ai import RunContext

from k_pilot.application.deps import AppDeps


async def list_windows(ctx: RunContext[AppDeps]) -> str:
    """
    Lista todas las ventanas abiertas en KDE.

    Retorna IDs, títulos y estados para que puedas referirte a ellas
    en otras herramientas como focus_window o close_window.
    """
    windows = await ctx.deps.window_port.list_windows()  # <-- Agregar await

    if not windows:
        return "No hay ventanas visibles o KWin no responde."

    lines = ["Ventanas abiertas:"]
    for w in windows:
        status = "🟢" if w.is_active else "⚪"
        if w.is_minimized:
            status = "🟡"
        lines.append(
            f"{status} ID:{w.id} | {w.title} ({w.app_name}) [Escritorio {w.desktop}]"
        )

    return "\n".join(lines)


async def focus_window(ctx: RunContext[AppDeps], window_id: str) -> str:
    """
    Trae una ventana al frente y le da foco.

    Args:
        window_id: El ID retornado por list_windows (ej: "0x123456")
    """
    result = ctx.deps.window_port.focus_window(window_id)
    return result.message


async def close_window(
    ctx: RunContext[AppDeps], window_id: str, force: bool = False
) -> str:
    """
    Cierra una ventana. Si tiene cambios sin guardar, KDE preguntará al usuario.

    Args:
        window_id: ID de la ventana
        force: Si es True, intenta cerrar sin confirmar (cuidado!)
    """
    # Nota: force=True requeriría señal SIGKILL, por ahora solo graceful close
    result = ctx.deps.window_port.close_window(window_id)
    return result.message
