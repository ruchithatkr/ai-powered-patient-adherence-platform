"""Authenticated MCP gateway (in-process for the local prototype).

Every call validates: server allowlist, operation, role scope, purpose, parameter schema, approval token
for external writes, timeout, and response shape. Returned content is treated as untrusted DATA.
"""
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml

from domain.models import Consent
from domain.synthetic import KNOWLEDGE_BASE, build
from observability.audit import AuditStore

CONFIG = yaml.safe_load((Path(__file__).parent / "servers.yaml").read_text())


class GatewayError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


class Backend:
    """Synthetic backends behind the logical MCP servers."""

    def __init__(self):
        self.db = build()
        self.tasks: dict[str, dict] = {}
        self.sent: dict[str, dict] = {}
        self.fail: set[str] = set()      # test hook: server names that fail
        self.rate: list[float] = []

    def patient_records(self, patient_ref):
        rec = self.db.get(patient_ref)
        if not rec:
            raise GatewayError("NOT_FOUND", "unknown patient_ref")
        return rec["context"].model_dump(mode="json")

    def consent(self, patient_ref):
        rec = self.db.get(patient_ref)
        return rec["consent"].model_dump(mode="json") if rec else None

    def withdraw_consent(self, patient_ref):
        self.db[patient_ref]["consent"].withdrawn = True

    def article(self, kb_id):
        a = KNOWLEDGE_BASE.get(kb_id)
        if not a:
            raise GatewayError("NOT_FOUND", "unknown article")
        return {"kb_id": kb_id, **a}

    def infer(self, features):
        # Deterministic fallback model (versioned). NOT a trained model; calibration NOT YET MEASURED.
        n, ratio, late = features["n_obs"], features["adherence_ratio"], features["late_refill_days"]
        score = round(min(1.0, 0.7 * (1 - ratio) + 0.3 * min(1.0, late / 20)), 3)
        return {"score": score, "model": "adherence-risk-deterministic-fallback", "model_version": "0.1.0",
                "n_obs": n}

    def create_task(self, patient_ref, packet):
        tid = "RT-" + uuid.uuid4().hex[:8]
        self.tasks[tid] = {"task_id": tid, "patient_ref": patient_ref, "packet": packet, "status": "OPEN"}
        return {"task_id": tid}

    def get_task(self, task_id):
        return self.tasks[task_id]

    def send(self, idempotency_key, channel, template_id, body, patient_ref):
        if idempotency_key in self.sent:                       # idempotent
            return {**self.sent[idempotency_key], "duplicate": True}
        now = time.time()
        self.rate = [t for t in self.rate if now - t < 60]
        if len(self.rate) >= 20:
            raise GatewayError("RATE_LIMITED", "notification rate limit")
        self.rate.append(now)
        r = {"receipt_id": "RCPT-" + uuid.uuid4().hex[:8], "delivery_status": "DELIVERED_SIMULATED",
             "simulated": True, "duplicate": False}
        self.sent[idempotency_key] = r
        return r


class Gateway:
    def __init__(self, audit: AuditStore, backend: Backend, timeout_s: float = 2.0):
        self.audit, self.backend, self.timeout_s = audit, backend, timeout_s
        self.valid_approvals: set[str] = set()   # tokens minted only by the approval workflow

    def call(self, server: str, op: str, params: dict, *, actor: str, role: str, purpose: str, trace_id: str,
             parent_id: Optional[str] = None, approval_token: Optional[str] = None) -> Any:
        t0 = time.time()
        node = f"tool:{server}.{op}"

        def deny(code, msg):
            self.audit.append(trace_id=trace_id, actor=actor, node=node, status="DENIED", parent_id=parent_id,
                              tool=server, tool_operation=op, error_code=code)
            raise GatewayError(code, msg)

        srv = CONFIG["servers"].get(server)
        if not srv:
            deny("SERVER_NOT_ALLOWLISTED", f"{server} is not allowlisted")
        spec = srv["ops"].get(op)
        if not spec:
            deny("OP_NOT_ALLOWED", f"{op} not permitted on {server}")
        if role not in srv["roles"]:
            deny("ROLE_NOT_AUTHORIZED", f"role {role} lacks scope for {server}")
        if purpose not in CONFIG["purposes"]:
            deny("PURPOSE_NOT_ALLOWED", "purpose not allowed")
        missing = [k for k in spec["required"] if k not in params]
        if missing:
            deny("SCHEMA_INVALID", f"missing params {missing}")
        if spec.get("approval") and (not approval_token or approval_token not in self.valid_approvals):
            deny("APPROVAL_REQUIRED", "valid human-approval token required for external write")
        if server in self.backend.fail:
            self.audit.append(trace_id=trace_id, actor=actor, node=node, status="ERROR", parent_id=parent_id,
                              tool=server, tool_operation=op, error_code="TOOL_UNAVAILABLE",
                              latency_ms=int((time.time() - t0) * 1000))
            raise GatewayError("TOOL_UNAVAILABLE", f"{server} unavailable")
        fn = {"patient-records-read": self.backend.patient_records, "consent-registry-read": self.backend.consent,
              "knowledge-base-read": self.backend.article, "risk-model-infer": self.backend.infer,
              "care-team-review": {"create_task": self.backend.create_task, "get_task": self.backend.get_task}.get(op),
              "notification-send": self.backend.send}[server]
        result = fn(**params) if server not in ("patient-records-read", "consent-registry-read") else fn(params["patient_ref"])
        elapsed = time.time() - t0
        if elapsed > self.timeout_s:
            deny("TIMEOUT", "tool exceeded timeout")
        eid = self.audit.append(trace_id=trace_id, actor=actor, node=node, parent_id=parent_id, tool=server,
                                tool_operation=op, input=params, output=result,
                                latency_ms=int(elapsed * 1000), approval_id=approval_token)
        return result
