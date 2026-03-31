import asyncio
from typing import Any, Callable

import lwake  # type: ignore

from k_pilot.domain.ports import WakeWordPort
from k_pilot.infrastructure.logging import get_logger

logger = get_logger(layer="infrastructure", component="local_wake")


class LocalWakeAdapter(WakeWordPort):
    def __init__(
        self, reference_folder: str = "live/wake_words", threshold: float = 0.1
    ):
        self.reference_folder = reference_folder
        self.threshold = threshold
        self._is_listening = False
        self._task: asyncio.Task[None] | None = None

    async def start_listening(
        self, on_detected: Callable[[dict[str, Any], Any], None]
    ) -> None:
        self._is_listening = True
        logger.info(
            "wakeword.listening",
            folder=self.reference_folder,
            threshold=self.threshold,
        )

        loop = asyncio.get_running_loop()

        def _blocking_listen() -> None:
            try:
                lwake.listen(
                    self.reference_folder,
                    threshold=self.threshold,
                    method="embedding",
                    callback=on_detected,
                    buffer_size=1.0,
                    slide_size=0.1,
                )
            except Exception as e:
                logger.error("wakeword.error", error=str(e))
            finally:
                self._is_listening = False

        self._task = loop.run_in_executor(None, _blocking_listen)  # type: ignore

    async def stop_listening(self) -> None:
        self._is_listening = False
        # lwake doesn't seem to expose a direct stop method in Python short of terminating the thread/process or
        # using internal locks. We'll simply let it run but we could ignore callbacks or kill the executor if needed.
        logger.info("wakeword.stopping")
