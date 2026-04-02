"""Configuración centralizada de logging estructurado."""

import logging
import os
import sys
from dataclasses import dataclass
from enum import Enum

import structlog


class LogLevel(str, Enum):
    """
    Niveles de log válidos según el estándar de logging de Python.

    Usamos str+Enum para que Pydantic (si lo usas) o cualquier validador
    pueda comparar directamente: LogLevel.DEBUG == "DEBUG" → True
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class LoggingConfig:
    """
    Configuración inmutable de logging.

    frozen=True: Una vez creada, no se puede modificar (thread-safe).
    """

    level: LogLevel = LogLevel.INFO
    json_format: bool = False

    @property
    def python_level(self) -> int:
        """Convierte el enum al valor numérico de logging (10, 20, 30...)."""
        return getattr(logging, self.level.value)


def load_logging_config() -> LoggingConfig:
    """
    Carga configuración desde variables de entorno con validación estricta.

    Orden de prioridad:
        1. K_PILOT_LOG_LEVEL (específico de tu app)
        2. LOG_LEVEL (genérico, fallback común)
        3. "INFO" (default seguro)

    Raises:
        ValueError: Si el nivel especificado no existe en LogLevel.
    """
    # --- 1. Obtener valor crudo del entorno ---
    # Buscamos primero la variable específica, luego la genérica
    raw_value = os.getenv("K_PILOT_LOG_LEVEL") or os.getenv("LOG_LEVEL")

    # --- 2. Aplicar default si no hay nada ---
    if raw_value is None:
        return LoggingConfig(level=LogLevel.INFO, json_format=False)

    # --- 3. Limpiar el valor (strip + uppercase para case-insensitive) ---
    cleaned = raw_value.strip().upper()

    # --- 4. Validación estricta ---
    # Verificamos que el valor limpio exista en nuestro Enum
    if cleaned not in LogLevel._value2member_map_:
        # Si no existe, listamos los válidos para ayudar al usuario
        valid_levels = [level.value for level in LogLevel]
        raise ValueError(
            f"Variable de entorno LOG_LEVEL inválida: '{raw_value}'\\n"
            f"Valor recibido tras limpieza: '{cleaned}'\\n"
            f"Valores permitidos: {', '.join(valid_levels)}"
        )

    # --- 5. Construir la configuración ---
    level = LogLevel(cleaned)  # Creamos el enum desde el string validado

    # Leemos JSON format (booleano simple)
    json_raw = os.getenv("K_PILOT_LOG_JSON", "").lower()
    json_format = json_raw in ("1", "true", "yes", "on")

    return LoggingConfig(level=level, json_format=json_format)


def configure_logging(config: LoggingConfig | None = None) -> LoggingConfig:
    """
    Inicializa structlog con la configuración proporcionada o la carga del entorno.

    Args:
        config: Configuración pre-construida. Si es None, se carga del entorno.

    Returns:
        La configuración efectiva usada (útil para debugging).
    """
    if config is None:
        config = load_logging_config()

    # --- Configurar procesadores según modo ---
    processors = [
        structlog.contextvars.merge_contextvars,  # Primero: carga contexto thread-local
        structlog.stdlib.add_log_level,  # Añade 'level': 'info'
        structlog.stdlib.add_logger_name,  # Añade 'logger': 'nombre'
        structlog.processors.TimeStamper(fmt="iso", utc=config.json_format),
    ]

    if config.json_format:
        # Modo Producción: JSON estructurado para parsers
        processors.extend(
            [
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,  # Formatea excepciones como string
                structlog.processors.dict_tracebacks,  # O como objeto estructurado (mejor para JSON)
                structlog.processors.JSONRenderer(),  # Output final: JSON
            ]
        )
        factory = structlog.stdlib.LoggerFactory()

        # Configurar logging de stdlib para que no interfiera
        logging.basicConfig(
            level=config.python_level,
            format="%(message)s",  # structlog maneja el formato
            stream=sys.stderr,
            force=True,
        )
    else:
        # Modo Desarrollo: Consola legible con colores
        processors.extend(
            [
                structlog.dev.set_exc_info,  # Marca exc_info para ConsoleRenderer
                structlog.dev.ConsoleRenderer(colors=True, pad_level=False),
            ]
        )
        factory = structlog.PrintLoggerFactory(sys.stderr)

    # --- Aplicar configuración a structlog ---
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(config.python_level),
        logger_factory=factory,
        cache_logger_on_first_use=True,
    )

    return config
