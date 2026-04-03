from typing import Any, TypeVar

T = TypeVar("T")


class AppDeps:
    """Contenedor dinámico de dependencias para K-Pilot."""

    def __init__(self) -> None:
        self._ports: dict[type, Any] = {}

    def register(self, port_type: type[T], implementation: T) -> None:
        """Registra un adaptador para un puerto específico."""
        self._ports[port_type] = implementation

    def get(self, port_type: type[T]) -> T:
        """Recupera la implementación de un puerto."""
        if port_type not in self._ports:
            raise RuntimeError(f"El puerto {port_type.__name__} no está registrado.")
        return self._ports[port_type]  # type: ignore
