"""Modelos internos del adapter AT-SPI (NO son del dominio)."""

from dataclasses import dataclass

from k_pilot.core.domain.common.value_objects import WindowRect
from k_pilot.core.domain.window.models import WindowInfo


@dataclass(frozen=True, slots=True)
class ATSPIWindowInfo:  # Renombrado para claridad
    """Modelo específico del adapter AT-SPI."""

    window_id: str
    title: str
    rect: WindowRect
    app_name: str = ""
    pid: int | None = None
    active: bool = False
    minimized: bool = False
    maximized: bool = False
    fullscreen: bool = False

    def to_domain(self) -> "WindowInfo":
        """Map a modelo de dominio."""
        from k_pilot.core.domain.window.models import WindowInfo

        return WindowInfo(
            id=self.window_id,
            title=self.title,
            app_name=self.app_name,
            is_active=self.active,
            is_minimized=self.minimized,
            desktop=None,  # AT-SPI no expone desktop directamente
        )
