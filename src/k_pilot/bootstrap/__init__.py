from dotenv import load_dotenv

from k_pilot.bootstrap.container import container
from k_pilot.core.shared.logging import configure_logging, get_logger


def bootstrap() -> None:
    # Environment variables
    load_dotenv()

    # Logging
    configure_logging()

    logger = get_logger("k-pilot.bootstrap")
    logger.info("bootstrap start")

    container.configure()

    logger.info("bootstrap complete")
