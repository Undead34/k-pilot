"""Observabilidad PydanticAI-compatible con inyección de logger."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from pydantic_ai import RunContext
from structlog import BoundLogger

from k_pilot.core.shared.logging import get_logger

P = ParamSpec("P")
R = TypeVar("R")

# Campos sensibles que se redactan automáticamente
REDACTED_FIELDS = frozenset(
    {
        "body",
        "command",
        "summary",
        "text",
        "user_input",
        "password",
        "token",
        "api_key",
        "secret",
        "content",
    }
)


def _generate_id() -> str:
    """Genera ID corto único para trazabilidad."""
    import uuid

    return uuid.uuid4().hex[:12]


def _redact_value(key: str, value: Any) -> Any:
    """Redacta valores sensibles y resume estructuras complejas."""
    key_lower = key.lower()

    # Redactar campos sensibles
    if any(sensitive in key_lower for sensitive in REDACTED_FIELDS):
        if isinstance(value, str):
            return f"<redacted len={len(value)}>"
        return "<redacted>"

    # Primitivos pasan directo
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # Resumir colecciones
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__} len={len(value)}>"
    if isinstance(value, dict):
        return f"<dict keys={sorted(value.keys())}>"

    # Objeto genérico
    return f"<{type(value).__name__}>"


def _extract_tool_args(
    signature: inspect.Signature, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Extrae y sanitiza argumentos de una tool, ignorando RunContext."""
    bound = signature.bind_partial(*args, **kwargs)
    bound.apply_defaults()

    sanitized: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        # Ignorar RunContext de PydanticAI (siempre es el primer arg típicamente)
        if isinstance(value, RunContext):
            sanitized["ctx"] = f"<RunContext deps={type(value.deps).__name__}>"
            continue
        sanitized[name] = _redact_value(name, value)

    return sanitized


def _format_duration(start_ns: int) -> float:
    """Calcula duración en ms desde timestamp en nanosegundos."""
    return round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)


def instrument_tool(
    name: str | None = None,
    *,
    logger: BoundLogger | None = None,
    redact_fields: set[str] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Decorador para instrumentar tools de PydanticAI.

    Args:
        name: Nombre override de la tool (default: nombre de la función)
        logger: Logger structlog (default: crea uno nuevo)
        redact_fields: Campos adicionales a redactar

    Ejemplo:
        @instrument_tool()
        async def mi_tool(ctx: RunContext, query: str) -> Result:
            ...
    """
    _logger = logger or get_logger("k_pilot.observability.tool")
    _redact = redact_fields or set()
    _redact.update(REDACTED_FIELDS)

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        tool_name = name or fn.__name__
        signature = inspect.signature(fn)

        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            call_id = _generate_id()
            start_ns = time.perf_counter_ns()

            # Extraer argumentos sanitizados
            safe_args = _extract_tool_args(signature, args, kwargs)

            # Log inicio
            _logger.info(
                "tool.started",
                tool_name=tool_name,
                tool_call_id=call_id,
                arguments=safe_args,
            )

            try:
                result = await fn(*args, **kwargs)

                # Log éxito
                _logger.info(
                    "tool.completed",
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    duration_ms=_format_duration(start_ns),
                    result_type=type(result).__name__,
                )
                return result

            except Exception as exc:
                _logger.exception(
                    "tool.failed",
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    duration_ms=_format_duration(start_ns),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise

        return wrapper

    return decorator


@dataclass(frozen=True, slots=True)
class AgentTelemetry:
    """Datos de telemetría para una ejecución de agente."""

    transport: str
    provider: str
    model: str
    session_id: str
    user_prompt: str
    history_length: int = 0


def _extract_usage(result: Any) -> dict[str, Any]:
    """Extrae métricas de uso (tokens) del resultado si están disponibles."""
    usage = getattr(result, "usage", None)
    if callable(usage):
        usage = usage()
    if usage is None:
        return {}

    # Pydantic model_dump si está disponible
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump(exclude_none=True)
        except Exception:
            pass

    if isinstance(usage, dict):
        return usage

    return {"usage_raw": str(usage)}


async def run_observed(
    agent_run: Callable[..., Awaitable[R]],
    *,
    telemetry: AgentTelemetry,
    deps: Any,
    message_history: list[Any] | None = None,
    logger: BoundLogger | None = None,
) -> R:
    """
    Ejecuta un agent.run() con observabilidad completa.

    Args:
        agent_run: Callable del agente (ej: agent.run)
        telemetry: Metadatos de la ejecución
        deps: Dependencias de PydanticAI
        message_history: Historial de mensajes previos
        logger: Logger structlog (default: crea uno nuevo)

    Ejemplo:
        result = await run_observed(
            agent.run,
            telemetry=AgentTelemetry(...),
            deps=my_deps,
            message_history=history,
        )
    """
    log = logger or get_logger("k_pilot.observability.agent")
    turn_id = _generate_id()
    start_ns = time.perf_counter_ns()
    history = message_history or []

    log.info(
        "agent.turn.started",
        transport=telemetry.transport,
        provider=telemetry.provider,
        model=telemetry.model,
        session_id=telemetry.session_id,
        turn_id=turn_id,
        history_length=telemetry.history_length or len(history),
        prompt_chars=len(telemetry.user_prompt),
    )

    try:
        result = await agent_run(
            telemetry.user_prompt,
            deps=deps,
            message_history=history,
        )

        # Extraer métricas del resultado
        extra = _extract_usage(result)

        log.info(
            "agent.turn.completed",
            duration_ms=_format_duration(start_ns),
            output_chars=len(str(getattr(result, "output", ""))),
            message_count=len(result.all_messages()) if hasattr(result, "all_messages") else None,
            **extra,
        )
        return result

    except Exception as exc:
        log.exception(
            "agent.turn.failed",
            duration_ms=_format_duration(start_ns),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
