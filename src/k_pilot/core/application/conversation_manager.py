# application/use_cases/conversation_manager.py
from typing import Awaitable, Callable, Optional

from domain.models import ConversationConfig, MediaChunk, Message, Modality, UsageStats
from domain.ports.connection import ConnectionPort
from domain.ports.cost import CostPort
from domain.ports.history import HistoryPort
from domain.ports.input import InputPort
from domain.ports.output import OutputPort


class ConversationManager:
    """
    Orquestador principal (Facade del dominio).
    Coordina los puertos sin saber cómo están implementados.
    """

    def __init__(
        self,
        connection: ConnectionPort,
        input_handler: InputPort,
        output_handler: OutputPort,
        history: HistoryPort,
        cost_tracker: CostPort,
        config: Optional[ConversationConfig] = None,
    ):
        self.connection = connection
        self.input = input_handler
        self.output = output_handler
        self.history = history
        self.cost = cost_tracker
        self.config = config or ConversationConfig()

        # Estado interno
        self._current_user_message: Optional[Message] = None
        self._current_model_message: Optional[Message] = None

    async def start(self) -> None:
        """Caso de uso: Iniciar conversación."""
        await self.connection.connect(self.config)

        # Configurar callbacks de salida
        self.output.on_media_chunk(self._handle_model_chunk)
        self.output.on_turn_complete(self._handle_turn_complete)
        self.output.on_interruption(self._handle_interruption)

    async def stop(self) -> None:
        """Caso de uso: Terminar conversación."""
        await self.output.stop_receiving()
        await self.connection.disconnect()

    async def send_text(self, text: str) -> None:
        """Caso de uso: Enviar mensaje de texto."""
        # Guardar en historial local
        msg = Message(role="user")
        msg.add_text(text)
        await self.history.save_message(msg)

        # Enviar al modelo
        await self.input.send_text(text, end_of_turn=True)

        # Iniciar recepción de respuesta
        self._current_model_message = Message(role="model")
        await self.output.start_receiving()

    async def send_audio_stream(self, audio_generator):
        """Caso de uso: Streaming de audio."""
        self._current_user_message = Message(role="user")

        async for chunk in audio_generator:
            await self.input.send_audio_chunk(chunk, end_of_turn=False)
            self._current_user_message.add_audio(chunk)

        # Finalizar turno
        await self.input.send_audio_chunk(b"", end_of_turn=True)
        await self.history.save_message(self._current_user_message)

        self._current_model_message = Message(role="model")
        await self.output.start_receiving()

    async def pause(self) -> None:
        """Caso de uso: Pausar."""
        await self.input.send_text("")  # Señal de pausa implícita

    async def resume(self) -> None:
        """Caso de uso: Reanudar con historial."""
        context = await self.history.get_context_for_resume()
        # Inyectar contexto en nueva sesión...

    def on_model_response(self, callback: Callable[[Message], Awaitable[None]]):
        """Registra callback para respuestas completas."""
        self._response_callback = callback

    async def _handle_model_chunk(self, chunk: MediaChunk):
        if self._current_model_message:
            self._current_model_message.chunks.append(chunk)

    async def _handle_turn_complete(self):
        if self._current_model_message:
            await self.history.save_message(self._current_model_message)
            if hasattr(self, "_response_callback"):
                await self._response_callback(self._current_model_message)
            self._current_model_message = None

    async def _handle_interruption(self):
        """Usuario interrumpió al modelo."""
        if self._current_model_message:
            # Guardar mensaje parcial
            await self.history.save_message(self._current_model_message)
            self._current_model_message = None

    def get_usage_report(self) -> UsageStats:
        return self.cost.get_stats()
