"""Tracing: every span is mirrored to OpenTelemetry (span/parent ids) and to the append-only audit store.

Context propagates through asyncio automatically (OTel uses contextvars), so nested spans get exact parent links.
Events are appended when a span ends (append-only); order is reconstructed via `started_at`.
"""

import contextvars
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

from domain.clock import Clock, SystemClock
from observability.audit import AuditStore

_BASE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("audit_base", default=None)
_provider_ready = False


def setup_tracing(console: bool = False) -> None:
    global _provider_ready
    if _provider_ready:
        return
    provider = TracerProvider()
    if console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _provider_ready = True


class Span:
    """Mutable handle used inside a span to record outcome details."""

    def __init__(self, span_id: str) -> None:
        self.span_id = span_id
        self.status = "OK"
        self.fields: dict[str, Any] = {}
        self.input: Any = None
        self.output: Any = None

    def set(self, **fields: Any) -> None:
        for k in ("input", "output"):
            if k in fields:
                setattr(self, k, fields.pop(k))
        self.fields.update(fields)


class Tracer:
    def __init__(self, audit: AuditStore, clock: Clock | None = None) -> None:
        self.audit = audit
        self.clock = clock or SystemClock()
        self._otel = trace.get_tracer("adherence-platform")

    @contextmanager
    def bind(self, **base: Any) -> Iterator[None]:
        """Attach request-scoped fields (request_id, workflow_version, policy_version) to every event in scope."""
        token = _BASE.set({**(_BASE.get() or {}), **base})
        try:
            yield
        finally:
            _BASE.reset(token)

    def _ids(self, otel_span: Any) -> tuple[str, str | None]:
        ctx = otel_span.get_span_context()
        parent = getattr(otel_span, "parent", None)
        return format(ctx.span_id, "016x"), (format(parent.span_id, "016x") if parent else None)

    @asynccontextmanager
    async def span(
        self, *, trace_id: str, node: str, actor: str, parent_span_id: str | None = None, **fields: Any
    ) -> AsyncIterator[Span]:
        started = self.clock.now()
        t0 = time.perf_counter()
        with self._otel.start_as_current_span(node) as otel_span:
            span_id, parent = self._ids(otel_span)
            sp = Span(span_id)
            sp.set(**fields)
            otel_span.set_attribute("workflow.trace_id", trace_id)
            try:
                yield sp
            except BaseException as exc:
                if sp.status == "OK":
                    sp.status = "ERROR"
                sp.fields.setdefault("error_code", getattr(exc, "code", type(exc).__name__))
                otel_span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                latency = round((time.perf_counter() - t0) * 1000, 3)
                self.audit.append(
                    trace_id=trace_id,
                    actor=actor,
                    node=node,
                    status=sp.status,
                    input=sp.input,
                    output=sp.output,
                    timestamp=self.clock.now(),
                    span_id=span_id,
                    parent_span_id=parent_span_id or parent,
                    started_at=started.isoformat(),
                    latency_ms=latency,
                    **{**(_BASE.get() or {}), **sp.fields},
                )

    def event(self, *, trace_id: str, node: str, actor: str, status: str = "OK", **fields: Any) -> str:
        """Leaf event parented to the currently active span."""
        current = trace.get_current_span().get_span_context()
        parent = format(current.span_id, "016x") if current.is_valid else None
        now: datetime = self.clock.now()
        return self.audit.append(
            trace_id=trace_id,
            actor=actor,
            node=node,
            status=status,
            timestamp=now,
            parent_span_id=fields.pop("parent_span_id", None) or parent,
            started_at=now.isoformat(),
            **{**(_BASE.get() or {}), **fields},
        )
