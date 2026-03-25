import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from k_pilot.infrastructure.adapters.mpris_media import MprisMediaAdapter

load_dotenv()

# Asegurar que los imports funcionen
sys.path.insert(0, str(Path(__file__).parent))

from k_pilot.application.agent import k_agent
from k_pilot.application.deps import AppDeps
from k_pilot.infrastructure.adapters.kwin_windows import KWinWindowAdapter
from k_pilot.infrastructure.adapters.notifications import FreedesktopNotificationAdapter
from k_pilot.infrastructure.dbus_connection import get_session_bus


async def async_main():
    """Lógica principal asíncrona."""
    print("🚀 Iniciando K-Pilot...")

    # Inicializar conexiones
    bus = get_session_bus()

    # Crear adapters (inyección de dependencias)
    deps = AppDeps(
        notification_port=FreedesktopNotificationAdapter(bus),
        window_port=KWinWindowAdapter(bus),
        media_port=MprisMediaAdapter(bus),  # <-- Agregar
    )

    # Verificar:
    if not deps.media_port.is_available():
        print("⚠️  Media: No hay reproductores MPRIS activos")
    else:
        print("✅ Media: OK")

    # Verificar disponibilidad
    if not deps.notification_port.is_available():
        print("⚠️  Servicio de notificaciones no disponible")
    else:
        print("✅ Notificaciones: OK")

    if not deps.window_port.is_available():
        print("⚠️  Ventanas: instala 'kdotool' (yay -S kdotool)")
    else:
        print("✅ Ventanas: OK")

    # Test inicial
    print("\n🧪 Test de notificación...")
    result = await k_agent.run(
        "Envía una notificación diciendo 'K-Pilot listo' con icono dialog-ok",
        deps=deps,
    )
    print(result.output)

    print("\n💬 Escribe comandos naturales (ej: 'muestra las ventanas', 'salir')")
    while True:
        try:
            user_input = input("> ")
            if user_input.lower() in ("salir", "exit", "quit", "q"):
                break

            result = await k_agent.run(user_input, deps=deps)
            print(result.output)

        except KeyboardInterrupt:
            print("\n👋 Cancelado")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

    print("👋 K-Pilot terminado.")


def main():
    """Entry point síncrono para el CLI (pyproject.toml)."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
