# Copyright 2026 K-Pilot Contributors
# SPDX-License-Identifier: LGPL-2.1-or-later
# pylint: disable=too-few-public-methods

"""
Local wake word detection adapter using lwake library.

This module provides on-device wake word detection without cloud dependencies,
using audio embeddings for pattern matching. Designed for low-latency
voice activation in privacy-sensitive environments.

Requirements:
    - local-wake library (https://github.com/st-matskevich/local-wake)
    - Reference audio samples in specified folder
    - Microphone access permissions

Note:
    local-wake operates in a blocking manner. This adapter manages the execution
    in a background thread pool to maintain asyncio compatibility.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import Empty
from typing import Any, Callable, ClassVar, Final, Protocol

import structlog

from k_pilot.core.application.ports import WakeWordPort

logger = structlog.get_logger("k-pilot.local_wake")


class WakeWordCallback(Protocol):
    """
    Protocol for wake word detection callbacks.

    Implementations should handle the detection event asynchronously
    or defer to appropriate handlers.

    Args:
        metadata: Detection metadata including confidence scores.
        audio_data: Raw audio data or reference to captured audio.
    """

    def __call__(self, metadata: dict[str, Any], audio_data: Any) -> None:
        """Handle wake word detection event."""


@dataclass(frozen=True, slots=True)
class WakeWordConfig:
    """
    Immutable configuration for wake word detection.

    Attributes:
        reference_folder: Path to directory containing reference audio samples.
        threshold: Detection confidence threshold (0.0 to 1.0, lower = more sensitive).
        method: Detection algorithm method ("embedding" recommended).
        buffer_size: Audio buffer size in seconds for analysis window.
        slide_size: Window slide step in seconds (overlap between buffers).
    """

    reference_folder: str = "live/wake_words"
    threshold: float = 0.1
    method: str = "embedding"
    buffer_size: float = 1.0
    slide_size: float = 0.1

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"Threshold must be in [0.0, 1.0], got {self.threshold}")
        if self.buffer_size <= 0:
            raise ValueError(f"Buffer size must be positive, got {self.buffer_size}")
        if self.slide_size <= 0 or self.slide_size > self.buffer_size:
            raise ValueError(
                f"Slide size must be positive and <= buffer_size, got {self.slide_size}"
            )


class WakeWordError(Exception):
    """Base exception for wake word detection errors."""

    def __init__(
        self,
        message: str,
        *,
        config: WakeWordConfig | None = None,
    ) -> None:
        super().__init__(message)
        self.config = config


class WakeWordAlreadyRunningError(WakeWordError):
    """Raised when attempting to start listening while already active."""

    pass


class WakeWordDetectionError(WakeWordError):
    """Raised when the detection engine fails unexpectedly."""

    pass


def _run_lwake_process(config: WakeWordConfig, queue: mp.SimpleQueue) -> None:  # type: ignore
    """Target function for the multiprocessing background worker."""

    def _local_callback(metadata: dict[str, Any], _audio: Any) -> None:
        try:
            queue.put(metadata)
        except Exception:
            pass

    import lwake  # type: ignore[import]

    try:
        lwake.listen(
            config.reference_folder,
            threshold=config.threshold,
            method=config.method,
            callback=_local_callback,
            buffer_size=config.buffer_size,
            slide_size=config.slide_size,
        )
    except Exception as exc:
        queue.put({"__error__": str(exc)})


class LocalWakeAdapter(WakeWordPort):
    """
    Production-grade local wake word detection adapter.

    Implements WakeWordPort using the lwake library for on-device inference.
    Runs detection in a background thread to prevent blocking the asyncio
    event loop while maintaining low latency.

    Thread Safety:
        This adapter is asyncio-safe but not thread-safe. All public methods
        should be called from the same event loop thread.

    Resource Management:
        The adapter uses a ThreadPoolExecutor for background processing.
        Call stop_listening() before discarding the adapter to ensure
        proper cleanup.

    Example:
        >>> config = WakeWordConfig(reference_folder="wake_words", threshold=0.15)
        >>> adapter = LocalWakeAdapter(config)
        >>>
        >>> def on_wake(metadata, audio):
        ...     print(f"Wake word detected! Confidence: {metadata.get('confidence')}")
        ...
        >>> await adapter.start_listening(on_wake)
        >>> # ... run for a while ...
        >>> await adapter.stop_listening()
    """

    # lwake detection parameters
    DEFAULT_TIMEOUT: ClassVar[float] = 30.0  # Seconds to wait for thread cleanup

    def __init__(
        self,
        config: WakeWordConfig | None = None,
        *,
        reference_folder: str | None = None,
        threshold: float | None = None,
    ) -> None:
        """
        Initialize the local wake word adapter.

        Supports either passing a complete WakeWordConfig object or individual
        parameters for backward compatibility.

        Args:
            config: Complete configuration object. If provided, other args ignored.
            reference_folder: Path to reference audio samples (if config not provided).
            threshold: Detection threshold (if config not provided).

        Raises:
            ValueError: If configuration parameters are invalid.
        """
        if config is not None:
            self._config: WakeWordConfig = config
        else:
            # Backward compatibility with positional args
            self._config = WakeWordConfig(
                reference_folder=reference_folder or "live/wake_words",
                threshold=threshold if threshold is not None else 0.1,
            )

        self._is_listening: bool = False
        self._task: asyncio.Task[None] | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._process: mp.Process | None = None
        self._queue: mp.SimpleQueue[Any] | None = None
        self._lock = asyncio.Lock()

        logger.debug(
            "local_wake.initialized",
            folder=self._config.reference_folder,
            threshold=self._config.threshold,
            method=self._config.method,
        )

    @property
    def is_listening(self) -> bool:
        """
        Check if the adapter is currently actively listening.

        Returns:
            True if detection is running, False otherwise.
            Note: May return True briefly after stop_listening() is called
            until the background thread terminates.
        """
        return self._is_listening

    @property
    def config(self) -> WakeWordConfig:
        """Get the current immutable configuration."""
        return self._config

    async def start_listening(self, on_detected: Callable[[dict[str, Any], Any], None]) -> None:
        """
        Start wake word detection in background.

        Begins listening on the default microphone and triggers the callback
        when the wake word pattern is detected with confidence above threshold.

        Args:
            on_detected: Callback function invoked on detection.

        Raises:
            WakeWordAlreadyRunningError: If already listening.
            WakeWordDetectionError: If the detection engine fails to start.

        Note:
            This method returns immediately. Detection runs in a background
            thread. Use stop_listening() to terminate.
        """
        if self._is_listening:
            raise WakeWordAlreadyRunningError(
                "Wake word detection already active",
                config=self._config,
            )

        self._is_listening = True
        logger.info(
            "wakeword.listening.started",
            folder=self._config.reference_folder,
            threshold=self._config.threshold,
            method=self._config.method,
        )

        loop = asyncio.get_running_loop()

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lwake_q")
        self._queue = mp.SimpleQueue()
        self._process = mp.Process(
            target=_run_lwake_process, args=(self._config, self._queue), daemon=True
        )

        async def _queue_monitor() -> None:
            while self._is_listening:
                try:
                    if self._queue is not None:
                        metadata = await loop.run_in_executor(self._executor, self._queue.get)
                        if "__stop__" in metadata:
                            break
                        if "__error__" in metadata:
                            raise WakeWordDetectionError(metadata["__error__"])
                        on_detected(metadata, None)
                except Empty:
                    continue
                except WakeWordDetectionError as exc:
                    logger.exception("wakeword.detection_error", error=str(exc))
                    loop.call_soon_threadsafe(lambda: setattr(self, "_is_listening", False))
                    break
                except Exception as exc:
                    if self._is_listening:
                        logger.error("wakeword.queue_monitor_error", error=str(exc))

            logger.debug("wakeword.monitor_task.finished")

        try:
            self._process.start()
            self._task = loop.create_task(_queue_monitor())
            self._task.add_done_callback(self._on_listening_done)

        except Exception as exc:
            self._is_listening = False
            if self._process and self._process.is_alive():
                self._process.terminate()
            await self._cleanup_executor()
            raise WakeWordDetectionError(
                f"Failed to start listening task: {exc}",
                config=self._config,
            ) from exc

    async def stop_listening(self) -> None:
        """
        Stop wake word detection.

        Signals the adapter to stop and cleans up background resources.

        Note:
            lwake does not expose a direct cancellation mechanism. This method
            sets the internal state to stop accepting new detections and
            cancels the background task. The underlying lwake thread may
            continue until the next audio buffer is processed.

        Returns:
            None

        Raises:
            WakeWordError: If cleanup fails unexpectedly.
        """
        if not self._is_listening:
            logger.debug("wakeword.stop_listening.ignored_not_listening")
            return

        logger.info("wakeword.stopping")
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(lambda: setattr(self, "_is_listening", False))

        if self._queue is not None:
            try:
                self._queue.put({"__stop__": True})
            except Exception:
                pass

        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1.0)
            if self._process.is_alive():
                self._process.kill()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("wakeword.stop_listening.timeout_waiting_for_task")
            except asyncio.CancelledError:
                logger.debug("wakeword.task_cancelled_cleanly")

        await self._cleanup_executor()
        logger.info("wakeword.stopped")

    def _on_listening_done(self, task: asyncio.Task[None]) -> None:
        """
        Callback invoked when the listening task completes.

        Handles logging for normal completion vs exceptions.
        """
        try:
            task.result()  # Will raise if task failed
            logger.info("wakeword.listening.completed_normally")
        except asyncio.CancelledError:
            logger.debug("wakeword.listing.task_cancelled")
        except Exception as exc:
            logger.exception("wakeword.listening.task_failed", error=str(exc))
            self._is_listening = False

    async def _cleanup_executor(self) -> None:
        """Clean up thread pool executor."""
        if self._executor is None:
            return

        logger.debug("wakeword.cleanup_executor.starting")

        # Shutdown executor without waiting (threads may be blocked on I/O)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._executor.shutdown, False)

        self._executor = None
        logger.debug("wakeword.cleanup_executor.completed")
