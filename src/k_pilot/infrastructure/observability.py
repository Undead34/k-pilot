"""Helpers de observabilidad para agent runs y tools."""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar
from uuid import uuid4

import structlog
from structlog.contextvars import bound_contextvars

logger = structlog.get_logger("k-pilot.observability")

P = ParamSpec("P")
R = TypeVar("R")

REDACTED_ARG_NAMES = {
    "body",
    "command",
    "summary",
    "text",
    "user_input",
}


def new_observability_id() -> str:
    return uuid4().hex[:12]


def duration_ms_from(start_ns: int) -> float:
    return round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)


def _sanitize_value(name: str, value: Any) -> Any:
    if name in REDACTED_ARG_NAMES and value is not None:
        if isinstance(value, str):
            return f"<redacted len={len(value)}>"
        return "<redacted>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__} len={len(value)}>"
    if isinstance(value, dict):
        return f"<dict keys={sorted(value.keys())}>"
    return f"<{type(value).__name__}>"


def sanitize_tool_arguments(
    signature: inspect.Signature, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    bound = signature.bind_partial(*args, **kwargs)
    sanitized: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        if name == "ctx":
            continue
        sanitized[name] = _sanitize_value(name, value)
    return sanitized


def instrument_tool(
    tool_name: str, fn: Callable[P, Awaitable[R]]
) -> Callable[P, Awaitable[R]]:
    """Envuelve una tool con logs uniformes y medicion de duracion."""
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        invocation_id = new_observability_id()
        safe_args = sanitize_tool_arguments(signature, args, kwargs)
        logger.info(
            "tool.started",
            tool_name=tool_name,
            tool_call_id=invocation_id,
            tool_args=safe_args,
        )
        start_ns = time.perf_counter_ns()

        with bound_contextvars(tool_name=tool_name, tool_call_id=invocation_id):
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                logger.exception(
                    "tool.failed",
                    tool_name=tool_name,
                    tool_call_id=invocation_id,
                    duration_ms=duration_ms_from(start_ns),
                    error=str(exc),
                )
                raise

        logger.info(
            "tool.completed",
            tool_name=tool_name,
            tool_call_id=invocation_id,
            duration_ms=duration_ms_from(start_ns),
            result_type=type(result).__name__,
        )
        return result

    return wrapped


def summarize_usage(result: Any) -> dict[str, Any]:
    usage = getattr(result, "usage", None)
    if callable(usage):
        usage = usage()
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump(exclude_none=True)  # type: ignore
        except Exception:
            return {"usage": str(usage)}
    if isinstance(usage, dict):
        return usage
    return {"usage": str(usage)}


@dataclass(frozen=True)
class AgentRunTelemetry:
    transport: str
    provider: str
    model: str
    session_id: str
    user_command: str
    history_length: int = 0


async def run_with_observability(
    run_callable: Callable[..., Awaitable[Any]],
    *,
    telemetry: AgentRunTelemetry,
    deps: Any,
    message_history: list[Any] | None = None,
) -> Any:
    """Ejecuta el agent run con contexto y timing consistentes."""
    turn_id = new_observability_id()
    start_ns = time.perf_counter_ns()
    history = message_history or []

    logger.info(
        "agent.turn.started",
        transport=telemetry.transport,
        provider=telemetry.provider,
        model=telemetry.model,
        session_id=telemetry.session_id,
        turn_id=turn_id,
        history_length=telemetry.history_length or len(history),
        command_chars=len(telemetry.user_command),
    )

    with bound_contextvars(
        session_id=telemetry.session_id,
        turn_id=turn_id,
        transport=telemetry.transport,
        provider=telemetry.provider,
        model=telemetry.model,
    ):
        try:
            result = await run_callable(
                telemetry.user_command,
                deps=deps,
                message_history=history,
            )
        except Exception as exc:
            logger.exception(
                "agent.turn.failed",
                duration_ms=duration_ms_from(start_ns),
                error=str(exc),
            )
            raise

    logger.info(
        "agent.turn.completed",
        duration_ms=duration_ms_from(start_ns),
        output_chars=len(str(getattr(result, "output", ""))),
        message_count=len(result.all_messages())
        if hasattr(result, "all_messages")
        else None,
        **summarize_usage(result),
    )
    return result
