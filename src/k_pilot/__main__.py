"""Punto de entrada principal para la ejecucion de K-Pilot."""

import asyncio
import sys

import structlog
from dotenv import load_dotenv

from k_pilot.infrastructure.logging import configure_logging


def main() -> None:
    """Orquesta el arranque de la aplicacion de forma segura y explicita."""
    # 1. Cargar variables de entorno ANTES que nada.
    load_dotenv()

    # 2. Configurar el logging. `configure_logging` ahora carga desde el entorno.
    config = configure_logging()

    # 3. AHORA, con todo configurado, importar y ejecutar la logica de la aplicacion.
    logger = structlog.get_logger("k-pilot.main")
    logger.info(
        "logging_initialized",
        level=config.level.value,
        json_mode=config.json_format,
    )

    try:
        from k_pilot import run_app

        asyncio.run(run_app())
    except KeyboardInterrupt:
        logger.info("k_pilot_shutdown_requested")
        sys.exit(0)
    except Exception as e:
        logger.fatal("application_crashed", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
