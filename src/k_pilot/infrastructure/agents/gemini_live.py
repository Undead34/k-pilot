import asyncio
import sys
from uuid import uuid4

import pyaudio
from google import genai  # type: ignore
from google.genai.types import (
    Content,
    FunctionDeclaration,
    FunctionResponse,
    LiveConnectConfig,
    Modality,
    Part,
    PrebuiltVoiceConfig,
    Schema,
    SpeechConfig,
    Tool,
    Type,
    VoiceConfig,
)
from k_pilot.application.agent import k_agent
from k_pilot.application.deps import AppDeps
from k_pilot.infrastructure.logging import get_logger, logging_context
from k_pilot.infrastructure.observability import (
    AgentRunTelemetry,
    run_with_observability,
)

logger = get_logger(layer="infrastructure", component="gemini_live")


class GeminiLiveServer:
    def __init__(
        self, deps: AppDeps, model_id: str = "gemini-2.5-flash-native-audio-latest"
    ):
        self.deps = deps
        self.model_id = model_id
        self.client = genai.Client()
        self.k_pilot_history = []
        self.session_id = uuid4().hex[:12]

        # Configuración de PyAudio optimizada
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True,
            frames_per_buffer=1024,
        )

    async def start(self):
        k_pilot_tool = FunctionDeclaration(
            name="ask_k_pilot",
            description="Pídele al agente interno (K-Pilot) que controle música, volumen, ventanas de KDE Plasma y ver y modificar apps nativas.",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "command": Schema(
                        type=Type.STRING,
                        description="Orden textual para el sistema operativo.",
                    )
                },
                required=["command"],
            ),
        )

        config = LiveConnectConfig(
            response_modalities=[Modality.AUDIO],
            speech_config=SpeechConfig(
                voice_config=VoiceConfig(
                    prebuilt_voice_config=PrebuiltVoiceConfig(voice_name="Aoede")
                )
            ),
            tools=[Tool(function_declarations=[k_pilot_tool])],
        )

        logger.info(
            "live_server.starting",
            model=self.model_id,
            provider="gemini",
            session_id=self.session_id,
        )

        try:
            with logging_context(
                session_id=self.session_id,
                transport="gemini_live",
                provider="gemini",
                model=self.model_id,
            ):
                async with self.client.aio.live.connect(
                    model=self.model_id, config=config
                ) as session:
                    self._session = session
                    print(
                        "🤖 Gemini Live Conectado (Escritorio KDE). Escribe 'salir' para terminar."
                    )

                    # --- 1. COLA DE AUDIO ASÍNCRONA ---
                    # Esto asegura que descargar datos y reproducir audio no colisionen
                    audio_queue = asyncio.Queue()

                    async def audio_worker():
                        """Consume el audio de la cola y lo reproduce en orden."""
                        while True:
                            data = await audio_queue.get()
                            if data is None:  # Señal de apagado
                                break
                            # Reproduce en un hilo para no bloquear a asyncio
                            await asyncio.to_thread(self.stream.write, data)
                            audio_queue.task_done()

                    worker_task = asyncio.create_task(audio_worker())

                    # --- 2. BUCLE DE TECLADO ---
                    async def input_loop():
                        while True:
                            user_input = await asyncio.to_thread(input, "👤 Tú: ")
                            if user_input.lower() in ("quit", "exit", "salir"):
                                raise asyncio.CancelledError

                            if user_input.strip():
                                await session.send_client_content(
                                    turns=Content(
                                        role="user",
                                        parts=[Part(text=user_input.strip())],
                                    )
                                )

                    # --- 3. BUCLE DE ESCUCHA BLINDADO ---
                    async def receive_loop():
                        while True:  # ¡ESTO MANTIENE VIVO AL AGENTE ENTRE TURNOS!
                            try:
                                async for chunk in session.receive():
                                    # Audio y Texto
                                    if (
                                        chunk.server_content
                                        and chunk.server_content.model_turn
                                    ):
                                        for (
                                            part
                                        ) in chunk.server_content.model_turn.parts:
                                            if part.inline_data:
                                                # Lo tiramos a la cola, sin esperar a que se reproduzca
                                                await audio_queue.put(
                                                    part.inline_data.data
                                                )
                                            if part.text:
                                                sys.stdout.write(
                                                    f"\r🤖 Gemini: {part.text}\n👤 Tú: "
                                                )
                                                sys.stdout.flush()

                                    # Herramientas (K-Pilot)
                                    elif chunk.tool_call:
                                        function_responses = []
                                        for fc in chunk.tool_call.function_calls:
                                            if fc.name == "ask_k_pilot":
                                                command = fc.args["command"]
                                                logger.info(
                                                    "live.delegate_to_kpilot",
                                                    command=command,
                                                )

                                                result = await run_with_observability(
                                                    k_agent.run,
                                                    telemetry=AgentRunTelemetry(
                                                        transport="gemini_live",
                                                        provider="deepseek",
                                                        model="deepseek:deepseek-chat",
                                                        session_id=self.session_id,
                                                        user_command=command,
                                                        history_length=len(
                                                            self.k_pilot_history
                                                        ),
                                                    ),
                                                    deps=self.deps,
                                                    message_history=self.k_pilot_history,
                                                )
                                                self.k_pilot_history = (
                                                    result.all_messages()
                                                )

                                                function_responses.append(
                                                    FunctionResponse(
                                                        name=fc.name,
                                                        id=fc.id,  # <-- ID vital para que no crashee
                                                        response={
                                                            "result": str(result.output)
                                                        },
                                                    )
                                                )

                                        if function_responses:
                                            await session.send_tool_response(
                                                function_responses=function_responses
                                            )
                            except Exception as e:
                                logger.error("receive_chunk_error", error=str(e))
                                break

                            # Evita un micro-bloqueo antes de volver a escuchar el siguiente turno
                            await asyncio.sleep(0.01)

                    # --- 4. LANZAR TODO ---
                    await asyncio.gather(input_loop(), receive_loop())

        except asyncio.CancelledError:
            pass  # Apagado limpio, no hacemos nada
        except Exception as e:
            logger.error("live_session.failed", error=str(e))
        finally:
            # --- 5. LIMPIEZA TOTAL AL SALIR ---
            if "audio_queue" in locals():
                await audio_queue.put(None)  # Le dice al worker que termine
            if "worker_task" in locals():
                await worker_task
            if hasattr(self, "stream") and self.stream.is_active():
                self.stream.stop_stream()
                self.stream.close()
            if hasattr(self, "audio"):
                self.audio.terminate()

    async def trigger_voice_activation(self) -> None:
        """Activado externamente (ej: por Wake Word o un botón)."""
        logger.info("live.voice_activation.triggered")
        import sys
        
        sys.stdout.write("\r🔔 ¡Activación por voz detectada!\n👤 Tú: ")
        sys.stdout.flush()

        if hasattr(self, "_session") and self._session:
            try:
                await self._session.send_client_content(
                    turns=Content(
                        role="user",
                        parts=[Part(text="[SISTEMA]: El usuario te ha despertado usando el Wake Word. Salúdalo muy brevemente listo para ayudar.")],
                    )
                )
            except Exception as e:
                logger.error("live.voice_activation.failed", error=str(e))

