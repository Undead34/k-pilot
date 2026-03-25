from typing import Literal

from pydantic_ai import RunContext

from k_pilot.application.deps import AppDeps
from k_pilot.domain.models import Notification, Priority


async def notify_user(
    ctx: RunContext[AppDeps],
    summary: str,
    body: str,
    priority: Literal["low", "normal", "high", "critical"] = "normal",
    icon: str = "dialog-information",
) -> str:
    """
    Envía una notificación nativa al escritorio KDE.

    Usa esto para:
    - Confirmar que una operación larga terminó
    - Alertar errores importantes
    - Informar estado del sistema

    Args:
        summary: Título breve (ej: "Descarga completada")
        body: Mensaje detallado
        priority: low (no interrumpe), normal, high (alerta sonora), critical (persistente)
        icon: Nombre de icono KDE (ej: "dialog-ok", "dialog-error", "download")
    """
    priority_map = {
        "low": Priority.LOW,
        "normal": Priority.NORMAL,
        "high": Priority.HIGH,
        "critical": Priority.CRITICAL,
    }

    notif = Notification(
        title=summary, body=body, priority=priority_map[priority], icon=icon
    )

    result = ctx.deps.notification_port.send(notif)

    if result.success:
        return f"✓ {result.message}"
    return f"✗ Error: {result.message}"
