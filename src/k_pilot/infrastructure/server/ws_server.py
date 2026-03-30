import asyncio
from uuid import uuid4

import websockets

from k_pilot.application.agent import k_agent
from k_pilot.application.deps import AppDeps
from k_pilot.infrastructure.logging import get_logger, logging_context
from k_pilot.infrastructure.observability import (
    AgentRunTelemetry,
    run_with_observability,
)

logger = get_logger(layer="infrastructure", component="ws_server")


class KPilotWebSocketServer:
    def __init__(self, deps: AppDeps, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.deps = deps

    async def handle_connection(self, websocket):
        """Maneja el ciclo de vida de un cliente conectado."""
        client_ip = websocket.remote_address[0]
        session_id = uuid4().hex[:12]
        logger.info("ws.client_connected", ip=client_ip, session_id=session_id)

        message_history = []

        try:
            with logging_context(
                session_id=session_id,
                transport="websocket",
                provider="deepseek",
                model="deepseek:deepseek-chat",
            ):
                async for message in websocket:
                    user_input = message.strip()
                    if not user_input:
                        continue

                    logger.info("ws.request_received", command=user_input)

                    result = await run_with_observability(
                        k_agent.run,
                        telemetry=AgentRunTelemetry(
                            transport="websocket",
                            provider="deepseek",
                            model="deepseek:deepseek-chat",
                            session_id=session_id,
                            user_command=user_input,
                            history_length=len(message_history),
                        ),
                        deps=self.deps,
                        message_history=message_history,
                    )

                    response_text = str(result.output)
                    message_history = result.all_messages()

                    await websocket.send(response_text)

        except websockets.exceptions.ConnectionClosed:
            logger.info("ws.client_disconnected", ip=client_ip, session_id=session_id)
        except Exception as e:
            logger.error("ws.handler_error", error=str(e))
            await websocket.send(f"❌ Error interno del agente: {str(e)}")

    async def start(self):
        """Inicia el servidor y se queda escuchando."""
        logger.info("ws.server_starting", host=self.host, port=self.port)
        async with websockets.serve(self.handle_connection, self.host, self.port):
            await asyncio.Future()
