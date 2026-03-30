```
deactivate 2>/dev/null || true
rm -rf .venv
uv python pin 3.14
python3.14 -m venv --system-site-packages .venv
source .venv/bin/activate
uv sync --python 3.14
```

## Observability

- `K_PILOT_LOG_LEVEL=DEBUG` para ver tiempos detallados de turns y tools.
- `K_PILOT_LOG_JSON=1` para emitir logs estructurados en JSON.
- Cada ejecucion del agente ahora emite `agent.turn.started` y `agent.turn.completed` con `session_id`, `turn_id`, `provider`, `model` y `duration_ms`.
- Cada tool emite `tool.started`, `tool.completed` o `tool.failed` con `tool_name`, `tool_call_id` y `duration_ms`.
- Los argumentos sensibles (`command`, `text`, `summary`, `body`) se redactan automaticamente en los logs.
