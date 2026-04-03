from dotenv import load_dotenv

from k_pilot.core.shared.logging import configure_logging, get_logger

from .container import container


def bootstrap() -> None:
    # Environment variables
    load_dotenv()

    # Logging
    configure_logging()

    logger = get_logger("k-pilot.bootstrap")
    logger.info("bootstrap_start")

    container.configure()

    logger.info("bootstrap_complete")
