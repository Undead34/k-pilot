import asyncio
import sys
from typing import TYPE_CHECKING

from k_pilot import bootstrap

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger


async def main_async() -> int:
    bootstrap()

    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(main_async())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nShutdown requested.")
        sys.exit(0)
    except Exception as e:
        import structlog

        logger: BoundLogger = structlog.get_logger("k-pilot.main")  # type: ignore
        logger.fatal("application_crashed", error=str(e), exc_info=True)
        sys.exit(1)


# import asyncio
# import time
# from typing import Any

# import structlog

# from k_pilot.container import container
# from k_pilot.infrastructure.adapters.wake_word import LocalWakeAdapter
# from k_pilot.infrastructure.agents.gemini_live import GeminiLiveServer


# async def run_app() -> None:
#     """Inicializa y ejecuta los componentes principales de la aplicacion."""
#     logger = structlog.get_logger("k-pilot.run_app")
#     deps = container.deps

#     server = GeminiLiveServer(deps=deps)
#     wake_word = LocalWakeAdapter(reference_folder="assets/hey k-pilot")

#     loop = asyncio.get_running_loop()
#     background_tasks = set()
#     last_trigger_time = 0.0

#     def on_wake_word_callback(metadata: dict[str, Any], audio_data: Any) -> None:
#         nonlocal last_trigger_time
#         now = time.monotonic()
#         if now - last_trigger_time < 3.0:
#             return
#         last_trigger_time = now

#         def _schedule_task() -> None:
#             task = asyncio.create_task(server.trigger_voice_activation())
#             background_tasks.add(task)

#             def _log_if_failed(t: asyncio.Task[Any]) -> None:
#                 if not t.cancelled() and t.exception():
#                     logger.error(
#                         "wake_activation_task_failed",
#                         error=str(t.exception()),
#                         exc_info=True,
#                     )

#             task.add_done_callback(background_tasks.discard)
#             task.add_done_callback(_log_if_failed)

#         loop.call_soon_threadsafe(_schedule_task)

#     await wake_word.start_listening(on_wake_word_callback)

#     try:
#         logger.info("k_pilot_started", status="listening")
#         await server.start()
#     finally:
#         await wake_word.stop_listening()
#         logger.info("k_pilot_shutdown", detail="cleanup_complete")
