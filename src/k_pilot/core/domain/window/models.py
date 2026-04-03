"""Entidades del contexto Window."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    """Modelo de dominio para ventanas (contrato del puerto)."""

    id: str
    title: str
    app_name: str
    is_active: bool
    is_minimized: bool
    desktop: int | None = None
