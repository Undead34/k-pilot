import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class WindowRect:
    # Coordenadas del área del cliente (contenido)
    x: float
    y: float
    width: float
    height: float

    # Coordenadas de la ventana completa (incluyendo bordes/decoraciones)
    window_x: float
    window_y: float
    window_width: float
    window_height: float

    # --- Propiedades de Conveniencia (Cálculos Geométricos) ---
    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def window_right(self) -> float:
        return self.window_x + self.window_width

    @property
    def window_bottom(self) -> float:
        return self.window_y + self.window_height

    # --- Márgenes del Window (Offsets) ---
    @property
    def border_left(self) -> float:
        return self.x - self.window_x

    @property
    def border_top(self) -> float:
        return self.y - self.window_y

    @property
    def border_right(self) -> float:
        return self.window_right - self.right

    @property
    def border_bottom(self) -> float:
        return self.window_bottom - self.bottom

    def to_dict(self, include_computed: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if include_computed:
            # Filtramos solo las propiedades definidas en la clase
            props = {
                name: getattr(self, name)
                for name in dir(self)
                if isinstance(getattr(type(self), name, None), property)
            }
            data.update(props)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass(slots=True, frozen=True)
class WindowInfo:
    window_id: str
    title: str
    rect: WindowRect
    app_name: str = ""

    pid: int | None = None
    active: bool = False
    minimized: bool = False
    maximized: bool = False
    fullscreen: bool = False
