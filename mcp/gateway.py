"""Authenticated MCP gateway (in-process for the local prototype).

Every call validates: server allowlist, operation, role scope, purpose, destination, parameter JSON-schema,
consent (for patient-scoped servers), human-approval token for external writes, timeout and response schema.
Returned content is untrusted DATA: it is schema-checked and screened for embedded instructions.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml
from jsonschema import Draft202012Validator

from domain.clock import Clock
from domain.models import Consent
from guardrails.checks import consent_decision, contains_injection
from observability.audit import h
from observability.metrics import Metrics
from observability.tracing import Span, Tracer

DENIED_CODES = {
    "SERVER_NOT_ALLOWLISTED", "OP_NOT_ALLOWED", "ROLE_NOT_AUTHORIZED", "PURPOSE_NOT_ALLOWED",
    "DESTINATION_NOT_ALLOWED", "SCHEMA_INVALID", "APPROVAL_REQUIRED", "CONSENT_NOT_ACTIVE",
}  # fmt: skip


class GatewayError(Exception):
    def __init__(self, code: str, msg: str) -> None:
        super().__init__(msg)
        self.code = code


class ToolAdapter(Protocol):
    async def invoke(self, op: str, params: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class CallIdentity:
    actor: str
    role: str
    purpose: str
    trace_id: str


class ApprovalRegistry:
    """Approval tokens are minted only by the approval flow and bound to one idempotency key and an expiry."""

    def __init__(self, clock: Clock, ttl_minutes: int = 60) -> None:
        self._clock, self._ttl = clock, timedelta(minutes=ttl_minutes)
        self._grants: dict[str, tuple[str, datetime]] = {}

    def grant(self, approval_id: str, idempotency_key: str) -> None:
        self._grants[approval_id] = (idempotency_key, self._clock.now() + self._ttl)

    def is_valid(self, token: str | None, idempotency_key: str | None) -> bool:
        if not token or token not in self._grants:
            return False
        bound_key, expires = self._grants[token]
        return bound_key == idempotency_key and expires > self._clock.now()


def load_config(path: Path) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(path.read_text())
    return cfg


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


ConsentLookup = Callable[[str], Awaitable[Consent | None]]


class Gateway:
    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        adapters: Mapping[str, ToolAdapter],
        tracer: Tracer,
        approvals: ApprovalRegistry,
        consent_lookup: ConsentLookup,
        clock: Clock,
        metrics: Metrics,
        timeout_s: float = 2.0,
    ) -> None:
        self.config, self.adapters = config, adapters
        self.tracer, self.approvals, self.clock, self.metrics = tracer, approvals, clock, metrics
        self._consent_lookup, self.timeout_s = consent_lookup, timeout_s
        self._validators: dict[tuple[str, str, str], Draft202012Validator] = {}
        for sname, srv in config["servers"].items():
            for oname, spec in srv["ops"].items():
                for kind in ("params", "response"):
                    self._validators[(sname, oname, kind)] = Draft202012Validator(spec[kind])

    def _deny(self, sp: Span, code: str, msg: str) -> GatewayError:
        sp.status = "DENIED" if code in DENIED_CODES else "ERROR"
        sp.set(error_code=code)
        return GatewayError(code, msg)

    async def call(
        self, server: str, op: str, params: dict[str, Any], *, ident: CallIdentity, approval_token: str | None = None
    ) -> Any:
        t0 = time.perf_counter()
        status = "ok"
        try:
            async with self.tracer.span(
                trace_id=ident.trace_id, node=f"tool:{server}.{op}", actor=ident.actor, tool=server, tool_operation=op
            ) as sp:
                return await self._call(sp, server, op, params, ident, approval_token)
        except GatewayError as e:
            status = e.code
            raise
        finally:
            self.metrics.inc("adherence_tool_calls_total", server=server, operation=op, status=status)
            self.metrics.observe("adherence_tool_latency_ms", (time.perf_counter() - t0) * 1000, server=server)

    async def _call(
        self, sp: Span, server: str, op: str, params: dict[str, Any], ident: CallIdentity, approval_token: str | None
    ) -> Any:
        srv = self.config["servers"].get(server)
        if not srv or server not in self.adapters:
            raise self._deny(sp, "SERVER_NOT_ALLOWLISTED", f"{server} is not allowlisted")
        spec = srv["ops"].get(op)
        if not spec:
            raise self._deny(sp, "OP_NOT_ALLOWED", f"{op} not permitted on {server}")
        sp.set(input_schema_hash=h(spec["params"]))
        if ident.role not in srv["roles"]:
            raise self._deny(sp, "ROLE_NOT_AUTHORIZED", f"role {ident.role} lacks scope for {server}")
        if ident.purpose not in self.config["purposes"]:
            raise self._deny(sp, "PURPOSE_NOT_ALLOWED", "purpose not allowed")
        if srv["destination"] not in self.config["allowed_destinations"]:
            raise self._deny(sp, "DESTINATION_NOT_ALLOWED", "destination not allowlisted")
        if errors := list(self._validators[(server, op, "params")].iter_errors(params)):
            raise self._deny(sp, "SCHEMA_INVALID", f"invalid params: {errors[0].message[:120]}")
        if spec.get("approval") and not self.approvals.is_valid(approval_token, params.get("idempotency_key")):
            raise self._deny(sp, "APPROVAL_REQUIRED", "valid human-approval token required for external write")
        if srv.get("consent_required") and "patient_ref" in params:
            consent = await self._consent_lookup(params["patient_ref"])
            decision, policy, reason = consent_decision(consent, ident.purpose, params.get("channel"), self.clock.now())
            if decision != "ALLOW":
                raise self._deny(sp, "CONSENT_NOT_ACTIVE", f"consent check failed ({policy}: {reason})")
        try:
            result = await asyncio.wait_for(self.adapters[server].invoke(op, params), timeout=self.timeout_s)
        except TimeoutError:
            raise self._deny(sp, "TIMEOUT", "tool exceeded timeout") from None
        except GatewayError as e:
            raise self._deny(sp, e.code, str(e)) from None
        except Exception:
            raise self._deny(sp, "TOOL_ERROR", "tool failed") from None  # never leak internals
        if errors := list(self._validators[(server, op, "response")].iter_errors(result)):
            raise self._deny(sp, "RESPONSE_INVALID", f"invalid response: {errors[0].message[:120]}")
        if any(contains_injection(s) for s in _strings(result)):
            sp.status = "BLOCKED"
            sp.set(error_code="UNTRUSTED_CONTENT", guardrail_decision="BLOCK")
            raise GatewayError("UNTRUSTED_CONTENT", "tool response contained instruction-like content")
        sp.set(input=params, output=result, approval_id=approval_token if spec.get("approval") else None)
        return result
