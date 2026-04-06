from k_pilot.core.application.app_deps import AppDeps


class KPilotWebSocketServer:
    def __init__(self, deps: AppDeps, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.deps = deps

    async def handle_connection(self, _):
        """Maneja el ciclo de vida de un cliente conectado."""
        pass

    async def start(self):
        """Inicia el servidor y se queda escuchando."""
        pass
