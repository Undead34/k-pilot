from pydantic_ai import RunContext

from k_pilot.bootstrap.container import AppDeps
from k_pilot.core.shared.logging import get_logger


async def list_windows(ctx: RunContext[AppDeps]) -> str:
    """
    Lista todas las ventanas abiertas en KDE.

    Retorna IDs, títulos y estados para que puedas referirte a ellas
    en otras herramientas como focus_window o close_window.
    """
    logger = get_logger("k-pilot.window_tools")

    logger.info("use_case.window.list.started")
    windows = await ctx.deps.window_port.list_windows()

    if not windows:
        logger.info("use_case.window.list.empty")
        return "No hay ventanas visibles o KWin no responde."

    lines = ["Ventanas abiertas:"]
    for w in windows:
        status = "🟢" if w.is_active else ("🟡" if w.is_minimized else "⚪")
        desk_str = f" [Escritorio {w.desktop}]" if w.desktop is not None else ""

        lines.append(f"{status} ID:{w.id} | {w.title} ({w.app_name}){desk_str}")

    logger.info("use_case.window.list.completed", count=len(windows))
    return "\n".join(lines)


async def focus_window(ctx: RunContext[AppDeps], window_id: str) -> str:
    """
    Trae una ventana al frente y le da foco.

    Args:
        window_id: El ID retornado por list_windows (ej: "0x123456")
    """
    logger = get_logger("k-pilot.window_tools")

    logger.info("use_case.window.focus.started", window_id=window_id)
    result = await ctx.deps.window_port.focus_window(window_id)
    logger.info(
        "use_case.window.focus.completed",
        window_id=window_id,
        success=result.success,
    )
    return f"{'✓' if result.success else '✗'} {result.message}"


async def close_window(ctx: RunContext[AppDeps], window_id: str, force: bool = False) -> str:
    """
    Cierra una ventana. Si tiene cambios sin guardar, KDE preguntará al usuario.

    Args:
        window_id: ID de la ventana
        force: (Ignorado actualmente) Si es True, intenta cerrar sin confirmar.
    """
    logger = get_logger("k-pilot.window_tools")

    logger.info("use_case.window.close.started", window_id=window_id, force=force)
    result = await ctx.deps.window_port.close_window(window_id)
    logger.info(
        "use_case.window.close.completed",
        window_id=window_id,
        success=result.success,
    )
    return f"{'✓' if result.success else '✗'} {result.message}"
