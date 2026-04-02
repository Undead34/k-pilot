# Copyright 2026 K-Pilot Contributors
# SPDX-License-Identifier: LGPL-2.1-or-later
# pylint: disable=too-many-instance-attributes,too-many-locals

"""
Gemini Live Server with native audio and K-Pilot integration.

Real-time voice conversation server using Google's Gemini 2.5 Flash with
native audio output. Integrates with K-Pilot agent for system control
(music, windows, notifications) via function calling.

Architecture:
    - Async audio queue with dedicated worker
    - Dual input: text (stdin) + voice activation (wake word)
    - Automatic tool delegation to K-Pilot agent
    - Graceful shutdown with resource cleanup

Requirements:
    - google-genai >= 0.5.0
    - PyAudio >= 0.2.11
    - Valid GEMINI_API_KEY environment variable
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final

import pyaudio
from google import genai  # type: ignore[import]
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

import structlog
from structlog.contextvars import bound_contextvars

from k_pilot.application.agent import k_agent
from k_pilot.application.deps import AppDeps
from k_pilot.infrastructure.observability import (
    AgentRunTelemetry,
    run_with_observability,
)

if TYPE_CHECKING:
    from google.genai.live import AsyncSession  # type: ignore[import]

logger = structlog.get_logger("k-pilot.gemini_live")


class GeminiLiveError(Exception):
    """Base exception for Gemini Live server errors."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id


class AudioDeviceError(GeminiLiveError):
    """Raised when audio initialization or playback fails."""

    pass


class SessionConnectionError(GeminiLiveError):
    """Raised when connection to Gemini Live API fails."""

    pass


class CommandExit(Exception):
    """Internal exception for graceful shutdown command."""

    pass


class VoiceName(StrEnum):
    """Available Gemini voice options."""

    AOEDE = "Aoede"
    CHARM = "Charm"
    CHIRP = "Chirp"  # Default en algunas regiones
    FENN = "Fenn"
    KORE = "Kore"
    PUCK = "Puck"


@dataclass(frozen=True, slots=True)
class AudioConfig:
    """
    PyAudio configuration for output stream.

    Optimized for Gemini's native audio output (24kHz, mono, 16-bit).
    """

    format: int = pyaudio.paInt16
    channels: int = 1
    rate: int = 24000  # Gemini native sample rate
    frames_per_buffer: int = 1024
    output: bool = True


@dataclass(frozen=True, slots=True)
class GeminiConfig:
    """
    Configuration for Gemini Live connection.

    Attributes:
        model_id: Model identifier (default: gemini-2.5-flash-native-audio-latest).
        voice: Voice name for speech synthesis.
        response_modalities: Output modalities (AUDIO for voice responses).
    """

    model_id: str = "gemini-2.5-flash-native-audio-latest"
    voice: VoiceName = VoiceName.AOEDE
    response_modalities: tuple[Modality, ...] = (Modality.AUDIO,)

    def to_live_config(self) -> LiveConnectConfig:
        """Convert to Google SDK LiveConnectConfig."""
        return LiveConnectConfig(
            response_modalities=list(self.response_modalities),
            speech_config=SpeechConfig(
                voice_config=VoiceConfig(
                    prebuilt_voice_config=PrebuiltVoiceConfig(
                        voice_name=self.voice.value
                    )
                )
            ),
            tools=[Tool(function_declarations=[self._build_k_pilot_tool()])],
        )

    @staticmethod
    def _build_k_pilot_tool() -> FunctionDeclaration:
        """Build the K-Pilot function declaration for tool calling."""
        return FunctionDeclaration(
            name="ask_k_pilot",
            description=(
                "Request the internal K-Pilot agent to control music, volume, "
                "KDE Plasma windows, and view/modify native applications."
            ),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "command": Schema(
                        type=Type.STRING,
                        description="Textual command for the operating system.",
                    )
                },
                required=["command"],
            ),
        )


class AudioPlayer:
    """
    Async-capable audio player using PyAudio.

    Manages the audio output stream lifecycle and provides async
    interface for writing audio chunks.

    Thread Safety:
        This class is designed for single-producer (asyncio thread)
        but uses thread-safe PyAudio callbacks internally.
    """

    def __init__(self, config: AudioConfig | None = None) -> None:
        self._config: Final[AudioConfig] = config or AudioConfig()
        self._pyaudio: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None

    def initialize(self) -> None:
        """Initialize PyAudio and open output stream."""
        try:
            self._pyaudio = pyaudio.PyAudio()
            self._stream = self._pyaudio.open(
                format=self._config.format,
                channels=self._config.channels,
                rate=self._config.rate,
                output=self._config.output,
                frames_per_buffer=self._config.frames_per_buffer,
            )
            logger.debug(
                "audio_player.initialized",
                rate=self._config.rate,
                channels=self._config.channels,
            )
        except Exception as exc:
            raise AudioDeviceError(f"Failed to initialize audio: {exc}") from exc

    async def play_chunk(self, data: bytes) -> None:
        """
        Play audio chunk asynchronously.

        Offloads blocking I/O to thread pool.

        Args:
            data: Raw PCM audio bytes.
        """
        if not self._stream:
            raise AudioDeviceError("Audio stream not initialized")

        await asyncio.to_thread(self._stream.write, data)

    def close(self) -> None:
        """Clean up audio resources."""
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception as exc:
                logger.warning("audio_player.stream_cleanup_error", error=str(exc))
            finally:
                self._stream = None

        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception as exc:
                logger.warning("audio_player.pyaudio_cleanup_error", error=str(exc))
            finally:
                self._pyaudio = None

        logger.debug("audio_player.closed")


class GeminiLiveServer:
    """
    Production-grade Gemini Live voice conversation server.

    Orchestrates real-time bidirectional communication with Google's
    Gemini API, including:
    - Audio output streaming (24kHz PCM)
    - Text input handling (stdin)
    - Voice activation triggers (wake word integration)
    - Tool calling delegation to K-Pilot agent

    Resource Management:
        Use as async context manager or call cleanup() explicitly:

        >>> server = GeminiLiveServer(deps)
        >>> try:
        ...     await server.start()
        ... finally:
        ...     await server.cleanup()
    """

    EXIT_COMMANDS: ClassVar[frozenset[str]] = frozenset({"quit", "exit", "salir"})
    AUDIO_QUEUE_SIZE: ClassVar[int] = 100  # Max pending audio chunks
    SHUTDOWN_SIGNAL: ClassVar[None] = None  # Sentinel for audio queue

    def __init__(
        self,
        deps: AppDeps,
        config: GeminiConfig | None = None,
    ) -> None:
        """
        Initialize the Gemini Live server.

        Args:
            deps: Application dependencies (adapters, config, etc.).
            config: Gemini connection configuration. Uses defaults if None.
        """
        self._deps: Final[AppDeps] = deps
        self._config: Final[GeminiConfig] = config or GeminiConfig()
        self._session_id: Final[str] = self._generate_session_id()

        # Google GenAI client
        self._client: Final[genai.Client] = genai.Client()
        self._session: AsyncSession | None = None

        # Audio subsystem
        self._audio_player: Final[AudioPlayer] = AudioPlayer()

        # State
        self._k_pilot_history: list[Any] = []
        self._shutdown_event: asyncio.Event = asyncio.Event()

        logger.info(
            "live_server.initialized",
            model=self._config.model_id,
            voice=self._config.voice.value,
            session_id=self._session_id,
        )

    @staticmethod
    def _generate_session_id() -> str:
        """Generate short unique session identifier."""
        import uuid

        return uuid.uuid4().hex[:12]

    async def start(self) -> None:
        """
        Start the live conversation session.

        Blocks until user issues exit command or connection drops.
        Handles graceful shutdown on interruption (Ctrl+C).

        Raises:
            SessionConnectionError: If connection to Gemini API fails.
            AudioDeviceError: If audio initialization fails.
        """
        self._audio_player.initialize()

        try:
            await self._run_session()
        except CommandExit:
            logger.info("live_server.user_exit")
        except Exception as exc:
            logger.exception("live_server.fatal_error")
            raise SessionConnectionError(
                f"Session failed: {exc}",
                session_id=self._session_id,
            ) from exc
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        """Clean up all resources (idempotent)."""
        logger.info("live_server.cleanup.starting")

        # Signal shutdown to all loops
        self._shutdown_event.set()

        # Close audio
        self._audio_player.close()

        # Note: Gemini session is auto-closed by context manager
        logger.info("live_server.cleanup.completed")

    async def trigger_voice_activation(self) -> None:
        """
        Trigger server activation from external source (wake word).

        Sends system prompt to Gemini indicating wake word was detected.

        Raises:
            SessionConnectionError: If no active session exists.
        """
        logger.info("live_server.voice_activation.triggered")

        if not self._session:
            raise SessionConnectionError(
                "Cannot trigger activation: no active session",
                session_id=self._session_id,
            )

        sys.stdout.write("\r🔔 ¡Activación por voz detectada!\n👤 Tú: ")
        sys.stdout.flush()

        try:
            await self._session.send_client_content(
                turns=Content(
                    role="user",
                    parts=[
                        Part(
                            text=(
                                "[SISTEMA]: El usuario te ha despertado usando el Wake Word. "
                                "Salúdalo muy brevemente listo para ayudar."
                            )
                        )
                    ],
                )
            )
        except Exception as exc:
            logger.error("live_server.voice_activation.failed", error=str(exc))
            raise

    async def _run_session(self) -> None:
        """Main session orchestration."""
        live_config = self._config.to_live_config()

        with bound_contextvars(
            session_id=self._session_id,
            transport="gemini_live",
            provider="google",
            model=self._config.model_id,
        ):
            try:
                async with self._client.aio.live.connect(
                    model=self._config.model_id,
                    config=live_config,
                ) as session:
                    self._session = session
                    print(
                        f"🤖 Gemini Live Conectado (Modelo: {self._config.model_id})\n"
                        f"   Escribe 'salir' o 'exit' para terminar.\n"
                        f"   Sesión: {self._session_id}\n"
                    )

                    await self._run_loops(session)

            except Exception as exc:
                raise SessionConnectionError(
                    f"Failed to establish Gemini session: {exc}"
                ) from exc

    async def _run_loops(self, session: AsyncSession) -> None:
        """
        Run concurrent input and receive loops.

        Args:
            session: Active Gemini Live session.
        """
        # Audio queue for decoupling network receive from playback
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)

        # Start audio worker
        audio_worker_task = asyncio.create_task(
            self._audio_worker(audio_queue),
            name="audio_worker",
        )

        try:
            await asyncio.gather(
                self._input_loop(session),
                self._receive_loop(session, audio_queue),
                return_exceptions=False,
            )
        except asyncio.CancelledError:
            pass  # Graceful shutdown
        finally:
            # Signal audio worker to stop
            await audio_queue.put(None)
            await audio_worker_task

    async def _input_loop(self, session: AsyncSession) -> None:
        """
        Handle user text input from stdin.

        Args:
            session: Active Gemini Live session.

        Raises:
            CommandExit: When user types exit command.
        """
        while not self._shutdown_event.is_set():
            try:
                user_input = await asyncio.to_thread(input, "👤 Tú: ")
                clean_input = user_input.strip().lower()

                if clean_input in self.EXIT_COMMANDS:
                    raise CommandExit

                if user_input.strip():
                    await session.send_client_content(
                        turns=Content(
                            role="user",
                            parts=[Part(text=user_input.strip())],
                        )
                    )

            except (EOFError, KeyboardInterrupt):
                raise CommandExit
            except CommandExit:
                raise
            except Exception as exc:
                logger.error("input_loop.error", error=str(exc))

    async def _receive_loop(
        self,
        session: AsyncSession,
        audio_queue: asyncio.Queue[bytes | None],
    ) -> None:
        """
        Process incoming server messages.

        Handles:
        - Audio chunks (queued for playback)
        - Text deltas (printed to stdout)
        - Tool calls (delegated to K-Pilot)

        Args:
            session: Active Gemini Live session.
            audio_queue: Queue for audio data to be played.
        """
        while not self._shutdown_event.is_set():
            try:
                async for chunk in session.receive():
                    await self._process_chunk(chunk, audio_queue, session)
                    await asyncio.sleep(0.01)  # Prevent tight loop

            except Exception as exc:
                logger.error("receive_loop.chunk_error", error=str(exc))
                await asyncio.sleep(0.5)  # Backoff on error

    async def _process_chunk(
        self,
        chunk: Any,
        audio_queue: asyncio.Queue[bytes | None],
        session: AsyncSession,
    ) -> None:
        """
        Process single server chunk.

        Args:
            chunk: Server content chunk.
            audio_queue: Audio playback queue.
            session: Session for sending tool responses.
        """
        # Handle model content (audio/text)
        if chunk.server_content and chunk.server_content.model_turn:
            for part in chunk.server_content.model_turn.parts:
                if part.inline_data:
                    # Queue audio for playback (non-blocking if queue full)
                    try:
                        audio_queue.put_nowait(part.inline_data.data)
                    except asyncio.QueueFull:
                        logger.warning("audio_queue.full", dropping=True)

                if part.text:
                    sys.stdout.write(f"\r🤖 Gemini: {part.text}\n👤 Tú: ")
                    sys.stdout.flush()

        # Handle tool calls (K-Pilot delegation)
        elif chunk.tool_call:
            await self._handle_tool_calls(chunk.tool_call, session)

    async def _handle_tool_calls(
        self,
        tool_call: Any,
        session: AsyncSession,
    ) -> None:
        """
        Execute K-Pilot tool calls and return responses.

        Args:
            tool_call: Tool call chunk from Gemini.
            session: Session for sending responses.
        """
        function_responses: list[FunctionResponse] = []

        for func_call in tool_call.function_calls:
            if func_call.name != "ask_k_pilot":
                logger.warning("unknown_tool_call", name=func_call.name)
                continue

            command = func_call.args.get("command", "")
            logger.info(
                "live_server.tool_call",
                tool="ask_k_pilot",
                command=command,
            )

            try:
                result = await self._execute_k_pilot_command(command)

                function_responses.append(
                    FunctionResponse(
                        name=func_call.name,
                        id=func_call.id,  # Required for correlation
                        response={"result": str(result)},
                    )
                )

            except Exception as exc:
                logger.exception("k_pilot.execution_failed")
                function_responses.append(
                    FunctionResponse(
                        name=func_call.name,
                        id=func_call.id,
                        response={"error": f"Execution failed: {exc}"},
                    )
                )

        if function_responses:
            await session.send_tool_response(function_responses=function_responses)

    async def _execute_k_pilot_command(self, command: str) -> Any:
        """
        Execute command via K-Pilot agent with observability.

        Args:
            command: Natural language command for K-Pilot.

        Returns:
            Agent execution result.
        """
        return await run_with_observability(
            k_agent.run,
            telemetry=AgentRunTelemetry(
                transport="gemini_live",
                provider="deepseek",
                model="deepseek:deepseek-chat",
                session_id=self._session_id,
                user_command=command,
                history_length=len(self._k_pilot_history),
            ),
            deps=self._deps,
            message_history=self.k_pilot_history,
        )

    async def _audio_worker(
        self,
        queue: asyncio.Queue[bytes | None],
    ) -> None:
        """
        Background worker for audio playback.

        Consumes audio chunks from queue and plays them sequentially.

        Args:
            queue: Audio data queue.
        """
        logger.debug("audio_worker.started")

        while True:
            try:
                data = await queue.get()

                if data is None:
                    break

                await self._audio_player.play_chunk(data)
                queue.task_done()

            except Exception as exc:
                logger.error("audio_worker.error", error=str(exc))

        logger.debug("audio_worker.stopped")

    @property
    def k_pilot_history(self) -> list[Any]:
        """Access K-Pilot conversation history."""
        return self._k_pilot_history

    @k_pilot_history.setter
    def k_pilot_history(self, value: list[Any]) -> None:
        """Update K-Pilot conversation history."""
        self._k_pilot_history = value
