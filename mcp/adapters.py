"""Synthetic MCP adapters behind the logical servers. Each adapter implements ToolAdapter (async invoke)."""

import asyncio
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from domain.clock import Clock
from domain.models import Consent
from domain.synthetic import build
from mcp.gateway import GatewayError
from observability.audit import AuditStore


class Backend:
    """Shared synthetic state + fault injection (used by tests and failure drills)."""

    def __init__(self, clock: Clock, kb_path: Path, rate_limit_per_min: int = 20) -> None:
        self.clock = clock
        self.db = build(clock.now())
        self.kb: dict[str, dict[str, Any]] = yaml.safe_load(kb_path.read_text())
        self.tasks: dict[str, dict[str, Any]] = {}
        self.sent: dict[str, dict[str, Any]] = {}
        self.fail: set[str] = set()
        self.delay: dict[str, float] = {}
        self.rate_limit = rate_limit_per_min
        self._rate: list[float] = []

    async def guard(self, server: str) -> None:
        if server in self.fail:
            raise GatewayError("TOOL_UNAVAILABLE", f"{server} unavailable")
        if d := self.delay.get(server):
            await asyncio.sleep(d)

    def get_consent(self, patient_ref: str) -> Consent | None:
        rec = self.db.get(patient_ref)
        return rec["consent"] if rec else None

    def withdraw_consent(self, patient_ref: str) -> None:
        self.db[patient_ref]["consent"].withdrawn = True


class PatientRecordsAdapter:
    def __init__(self, backend: Backend) -> None:
        self.b = backend

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        await self.b.guard("patient-records-read")
        rec = self.b.db.get(params["patient_ref"])
        if not rec:
            raise GatewayError("NOT_FOUND", "unknown patient_ref")
        return rec["context"].model_dump(mode="json")


class ConsentRegistryAdapter:
    def __init__(self, backend: Backend) -> None:
        self.b = backend

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        await self.b.guard("consent-registry-read")
        c = self.b.get_consent(params["patient_ref"])
        return c.model_dump(mode="json") if c else None


class KnowledgeBaseAdapter:
    def __init__(self, backend: Backend) -> None:
        self.b = backend

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        await self.b.guard("knowledge-base-read")
        a = self.b.kb.get(params["kb_id"])
        if not a:
            raise GatewayError("NOT_FOUND", "unknown article")
        return {"kb_id": params["kb_id"], **a}


class RiskModelAdapter:
    """Deterministic fallback model (versioned). NOT a trained model; calibration NOT YET MEASURED."""

    def __init__(self, backend: Backend) -> None:
        self.b = backend

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        await self.b.guard("risk-model-infer")
        f = params["features"]
        score = round(min(1.0, 0.7 * (1 - f["adherence_ratio"]) + 0.3 * min(1.0, f["late_refill_days"] / 20)), 3)
        return {"score": score, "model": "adherence-risk-deterministic-fallback", "model_version": "0.1.0"}


class CareTeamReviewAdapter:
    def __init__(self, backend: Backend) -> None:
        self.b = backend

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        await self.b.guard("care-team-review")
        if op == "create_task":
            tid = "RT-" + uuid.uuid4().hex[:8]
            self.b.tasks[tid] = {
                "task_id": tid, "patient_ref": params["patient_ref"], "packet": params["packet"],
                "status": "OPEN", "created_at": self.b.clock.now().isoformat(),
            }  # fmt: skip
            return {"task_id": tid}
        task = self.b.tasks.get(params["task_id"])
        if not task:
            raise GatewayError("NOT_FOUND", "unknown task")
        return task


class NotificationAdapter:
    def __init__(self, backend: Backend) -> None:
        self.b = backend

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        await self.b.guard("notification-send")
        key = params["idempotency_key"]
        if key in self.b.sent:  # idempotent
            return {**self.b.sent[key], "duplicate": True}
        now = time.monotonic()
        self.b._rate = [t for t in self.b._rate if now - t < 60]
        if len(self.b._rate) >= self.b.rate_limit:
            raise GatewayError("RATE_LIMITED", "notification rate limit")
        self.b._rate.append(now)
        receipt = {
            "receipt_id": "RCPT-" + uuid.uuid4().hex[:8], "delivery_status": "DELIVERED_SIMULATED",
            "simulated": True, "duplicate": False, "idempotency_key": key,
        }  # fmt: skip
        self.b.sent[key] = receipt
        return receipt


class AuditWriteAdapter:
    """Append-only: agents can add trace events but never update or delete."""

    def __init__(self, audit: AuditStore) -> None:
        self.audit = audit

    async def invoke(self, op: str, params: dict[str, Any]) -> Any:
        return {"event_id": self.audit.append(actor="agent", **params)}


def build_adapters(backend: Backend, audit: AuditStore) -> dict[str, Any]:
    return {
        "patient-records-read": PatientRecordsAdapter(backend),
        "consent-registry-read": ConsentRegistryAdapter(backend),
        "knowledge-base-read": KnowledgeBaseAdapter(backend),
        "risk-model-infer": RiskModelAdapter(backend),
        "care-team-review": CareTeamReviewAdapter(backend),
        "notification-send": NotificationAdapter(backend),
        "audit-write": AuditWriteAdapter(audit),
    }


__all__ = ["Backend", "build_adapters", "datetime"]
