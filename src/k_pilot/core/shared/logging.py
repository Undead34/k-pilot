import logging
import os
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog
from structlog.stdlib import BoundLogger


# Usamos str+Enum en lugar de IntEnum porque:
# 1. Los niveles de structlog son strings ("info", "debug")
# 2. Permite comparación directa con variables de entorno sin casteo
# 3. Es más explícito para desarrolladores que ven "DEBUG" en vez de 10
class LogLevel(str, Enum):
    """Niveles de severidad alineados con RFC 5424."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Alias de tipo para claridad semántica: un procesador no es cualquier Callable,
# es específicamente una función que transforma el event_dict de structlog.
# Esto ayuda a basedpyright a inferir tipos en la lista de procesadores.
Processor = Callable[
    [Any, str, MutableMapping[str, Any]],
    MutableMapping[str, Any] | str | bytes,
]


@dataclass(frozen=True)
class LoggingConfig:
    """Configuración inmutable del sistema de logging.

    frozen=True garantiza thread-safety: una vez creada la configuración,
    puede pasarse a múltiples threads sin riesgo de mutación accidental.
    Esto es crítico porque configure_logging() se llama al inicio de la app
    pero los loggers se usan concurrentemente después.
    """

    level: LogLevel = LogLevel.INFO
    json_format: bool = False

    @property
    def python_level(self) -> int:
        # Mapeo explícito en lugar de getattr(logging, self.value) porque:
        # 1. Evitamos dependencia dinámica con el módulo logging
        # 2. basedpyright puede verificar exhaustivamente que todos los casos están cubiertos
        # 3. Si alguien agrega un nivel al Enum pero olvida el mapeo, mypy/pyright fallan
        mapping: dict[LogLevel, int] = {
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.CRITICAL: logging.CRITICAL,
        }
        return mapping[self.level]


def load_logging_config() -> LoggingConfig:
    """Lee configuración desde el entorno con fallbacks razonables.

    Prioridad: K_PILOT_LOG_LEVEL > LOG_LEVEL > default(INFO)
    Esto permite override específico de la app sin romper convenciones genéricas
    de contenedores (que suelen setear LOG_LEVEL).
    """
    # Usamos 'or' en lugar de getenv con default porque queremos tratar
    # string vacío como "no especificado", no como valor válido.
    raw_value = os.getenv("K_PILOT_LOG_LEVEL") or os.getenv("LOG_LEVEL")

    if raw_value is None:
        # Default seguro: INFO evita spam de DEBUG en producción si alguien
        # olvida configurar la variable de entorno.
        return LoggingConfig(level=LogLevel.INFO, json_format=False)

    # Normalización agresiva: los usuarios escriben "debug", "Debug", "DEBUG ".
    # Strip elimina espacios accidentales; upper() estandariza comparación.
    cleaned = raw_value.strip().upper()

    # Validación temprana con fallo informativo: preferimos fallar rápido
    # al iniciar la app que descubrir en runtime que el logging no funciona.
    if cleaned not in LogLevel._value2member_map_:
        valid_levels = [level.value for level in LogLevel]
        raise ValueError(
            f"Variable de entorno LOG_LEVEL inválida: '{raw_value}'\n"
            f"Valor recibido tras limpieza: '{cleaned}'\n"
            f"Valores permitidos: {', '.join(valid_levels)}"
        )

    level = LogLevel(cleaned)

    # Booleano permisivo: aceptamos múltiples convenciones (1, true, yes)
    # porque diferentes equipos usan diferentes estándares en sus Dockerfiles.
    json_raw = os.getenv("K_PILOT_LOG_JSON", "").lower()
    json_format = json_raw in ("1", "true", "yes", "on")

    return LoggingConfig(level=level, json_format=json_format)


def configure_logging(config: LoggingConfig | None = None) -> LoggingConfig:
    """Inicializa el pipeline de procesamiento de logs.

    Structlog funciona como una cadena de procesadores: cada uno transforma
    el event_dict hasta llegar al renderer final. El orden es importante:
    los procesadores de contexto van primero, los de formato al final.
    """
    if config is None:
        config = load_logging_config()

    # Pipeline base: disponible en ambos modos (JSON y consola)
    # merge_contextvars debe ir primero para capturar variables de contexto
    # thread-local seteadas previamente (ej: request_id en middleware).
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,  # Injecta 'level': 'info' en el dict
        structlog.stdlib.add_logger_name,  # Permite filtrar por nombre de logger
        # UTC=True en producción para consistencia temporal entre servidores
        # UTC=False (local) en desarrollo para legibilidad del dev
        structlog.processors.TimeStamper(fmt="iso", utc=config.json_format),
    ]

    if config.json_format:
        # Modo producción: queremos parsing automatizado.
        # dict_tracebacks son mejores que strings para análisis de errores
        # porque permiten queries como "event.exception.type: ValueError".
        processors.extend(
            [
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,  # Fallback string por si acaso
                structlog.processors.dict_tracebacks,  # Estructura parseable
                structlog.processors.JSONRenderer(),  # Salida serializable
            ]
        )
        factory = structlog.stdlib.LoggerFactory()

        # Configuramos stdlib para que no interfiera: structlog maneja todo
        # el formato, stdlib solo debe pasar el mensaje crudo.
        # force=True sobreescribe cualquier config previa (ej: librerías
        # que llaman basicConfig() al importarse).
        logging.basicConfig(
            level=config.python_level,
            format="%(message)s",
            stream=sys.stderr,  # Logs van a stderr, no stdout (Unix philosophy)
            force=True,
        )
    else:
        # Modo desarrollo: optimizado para legibilidad humana.
        # ConsoleRenderer añade colores automáticamente si el tty lo soporta.
        processors.extend(
            [
                structlog.dev.set_exc_info,  # Marca excepciones para el renderer
                structlog.dev.ConsoleRenderer(colors=True, pad_level=False),
            ]
        )
        factory = structlog.stdlib.LoggerFactory()
        logging.basicConfig(
            level=config.python_level,
            format="%(message)s",
            stream=sys.stderr,
            force=True,
        )

    # Configuración global de structlog. cache_logger_on_first_use=True
    # mejora performance evitando re-configuración en subsiguientes get_logger().
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(config.python_level),
        logger_factory=factory,
        cache_logger_on_first_use=True,
    )

    return config


def get_logger(*args: object, **initial_values: object) -> BoundLogger:
    """Factory para obtener loggers tipados con contexto inicial.

    El type: ignore es necesario porque structlog.get_logger() retorna Any
    internamente por compatibilidad histórica, pero sabemos por contrato
    que con nuestra configuración siempre retorna BoundLogger.

    Usamos 'object' en lugar de 'Any' para los argumentos porque:
    - structlog acepta cualquier cosa como *args (nombre del logger) y
    - **initial_values (contexto estructurado inicial)
    - pero 'object' es más estricto que 'Any' manteniendo la flexibilidad necesaria.
    """
    return structlog.get_logger(*args, **initial_values)  # type: ignore[no-any-return]
