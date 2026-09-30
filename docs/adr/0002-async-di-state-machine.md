# ADR-0002: Async services, dependency injection, explicit state machine
Decision: FastAPI app factory + `Runtime` composition root (`app/container.py`); stages are handlers driven by `workflows/definition.py`;
policy/templates/patterns/MCP contracts live in versioned config, covered by `scripts/check_change_approvals.py`. Tracing uses OpenTelemetry span ids mirrored into the hash-chained audit log.
Consequences: no module-level singletons; deterministic tests via injected clock; parent-linked traces.
