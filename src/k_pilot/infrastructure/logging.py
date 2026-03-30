"""Configuracion centralizada de logging estructurado."""

import logging
import os
import sys
from contextlib import contextmanager
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, bound_contextvars, clear_contextvars


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure_logging() -> None:
    """Inicializa logging estructurado para toda la aplicacion."""
    level_name = os.getenv("K_PILOT_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper()
    json_logs = _env_flag("K_PILOT_LOG_JSON", default=False)
    log_level = getattr(logging, level_name, logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    logging.basicConfig(
        level=log_level, format="%(message)s", stream=sys.stderr, force=True
    )

    structlog.configure(
        processors=shared_processors
        + [
            structlog.processors.EventRenamer("event"),
            (
                structlog.processors.JSONRenderer()
                if json_logs
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def clear_logging_context() -> None:
    """Limpia el contexto request-scoped asociado al logger."""
    clear_contextvars()


def bind_logging_context(**values: Any) -> None:
    """Bindea pares clave/valor al contexto actual si tienen valor."""
    clean_values = {key: value for key, value in values.items() if value is not None}
    if clean_values:
        bind_contextvars(**clean_values)


@contextmanager
def logging_context(**values: Any):
    """Context manager para agregar contexto temporal a los logs."""
    clean_values = {key: value for key, value in values.items() if value is not None}
    with bound_contextvars(**clean_values):
        yield


def get_logger(*, layer: str, component: str):
    """Crea un logger etiquetado con la capa y el componente."""
    return structlog.get_logger(component).bind(layer=layer, component=component)
