"""Gestión singleton de conexiones D-Bus."""

from functools import lru_cache

from pydbus import SessionBus


@lru_cache(maxsize=1)
def get_session_bus() -> SessionBus:
    """Retorna singleton de SessionBus."""
    return SessionBus()


def check_interface_available(bus: SessionBus, interface: str, path: str) -> bool:
    """Verifica si una interfaz D-Bus existe."""
    try:
        bus.get(interface, path)
        return True
    except Exception:
        return False
