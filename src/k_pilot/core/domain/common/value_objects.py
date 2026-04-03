"""Value objects inmutables compartidos."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Result:
    success: bool
    message: str
    data: dict[str, Any] | None = None


# WindowRect solo aquí si es usado por múltiples contexts
# Si es solo para AT-SPI, moverlo al adapter
@dataclass(frozen=True, slots=True)
class WindowRect:
    x: float
    y: float
    width: float
    height: float
    window_x: float
    window_y: float
    window_width: float
    window_height: float
