from typing import Literal

from pydantic_ai import RunContext

from k_pilot.bootstrap.container import AppDeps
from k_pilot.core.domain.notification.models import Notification, Priority
from k_pilot.core.shared.logging import get_logger


async def notify_user(
    ctx: RunContext[AppDeps],
    summary: str,
    body: str,
    priority: Literal["low", "normal", "high", "critical"] = "normal",
    icon: str = "",
) -> str:
    """
    Envía una notificación nativa al escritorio KDE.

    REGLA VISUAL ESTRICTA (Icono vs Emoji):
    - Si usas un icono de aplicación (ej. "spotify", "firefox"), está PROHIBIDO usar emojis en el título o cuerpo.
    - Si el icono está vacío (""), DEBES usar un único emoji al inicio del título para dar contexto visual.
    - NUNCA uses icono de app y emoji al mismo tiempo.

    Args:
        summary: Título breve en una sola línea. Sin saltos de línea.
        body: Mensaje detallado. Soporta saltos de línea (\n) y HTML básico (<b>, <i>).
        priority: low (no interrumpe), normal, high (alerta sonora), critical (persistente).
        icon: Nombre de la app objetivo (ej: "vlc") o cadena vacía (""). PROHIBIDO usar iconos genéricos (ej: "dialog-info").
    """
    logger = get_logger("k-pilot.notification_tools")

    logger.info(
        "use_case.notification.send.started",
        priority=priority,
        has_icon=bool(icon),
        summary_chars=len(summary),
    )
    priority_map = {
        "low": Priority.LOW,
        "normal": Priority.NORMAL,
        "high": Priority.HIGH,
        "critical": Priority.CRITICAL,
    }

    notif = Notification(title=summary, body=body, priority=priority_map[priority], icon=icon)

    result = ctx.deps.notification_port.send(notif)

    logger.info(
        "use_case.notification.send.completed",
        success=result.success,
        priority=priority,
    )
    if result.success:
        return f"✓ {result.message}"
    return f"✗ Error: {result.message}"
