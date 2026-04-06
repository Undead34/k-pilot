"""Puertos (interfaces) del dominio."""

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WakeWordPort(Protocol):
    """Puerto para la detección del Wake Word (palabra clave de activación)."""

    async def start_listening(self, on_detected: Callable[[dict[str, Any], Any], None]) -> None:
        """
        Inicia la escucha en un hilo/tarea en segundo plano.
        Llama a `on_detected` cuando reconoce la palabra.
        """
        ...

    async def stop_listening(self) -> None:
        """Detiene el motor de detección."""
        ...
