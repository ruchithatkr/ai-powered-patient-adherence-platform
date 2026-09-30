"""Namespaced long-term memory + short-lived workflow state.

Long-term memory keeps only consented preferences, approved care-plan facts and intervention outcomes.
No raw chain-of-thought, no raw prompts, no patient free text. In-memory implementations sit behind
Protocols so an encrypted database/Redis backend can be swapped in without touching callers.
"""

import threading
from datetime import datetime, timedelta
from typing import Any, Protocol

from domain.clock import Clock
from domain.models import Consent, PriorIntervention, WorkflowState
from guardrails.checks import consent_decision


class ConflictError(Exception):
    pass


class WorkflowStoreProtocol(Protocol):
    def put(self, st: WorkflowState, expected_version: int | None = None) -> WorkflowState: ...
    def get(self, trace_id: str) -> WorkflowState | None: ...
    def get_by_request(self, request_id: str) -> WorkflowState | None: ...
    def purge_patient(self, patient_ref: str) -> int: ...


class WorkflowStore:
    """Short-term workflow state with TTL and optimistic locking."""

    def __init__(self, clock: Clock, ttl_hours: int = 24) -> None:
        self._d: dict[str, WorkflowState] = {}
        self._by_request: dict[str, str] = {}
        self._lock = threading.Lock()
        self.clock, self.ttl = clock, timedelta(hours=ttl_hours)

    def put(self, st: WorkflowState, expected_version: int | None = None) -> WorkflowState:
        with self._lock:
            cur = self._d.get(st.trace_id)
            if cur is not None and expected_version is not None and cur.version != expected_version:
                raise ConflictError("workflow modified concurrently")
            st.version = (cur.version + 1) if cur else 0
            st.expires_at = self.clock.now() + self.ttl
            self._d[st.trace_id] = st.model_copy(deep=True)
            self._by_request[st.request_id] = st.trace_id
            return st

    def get(self, trace_id: str) -> WorkflowState | None:
        with self._lock:
            st = self._d.get(trace_id)
            if st and st.expires_at and st.expires_at < self.clock.now():
                del self._d[trace_id]
                return None
            return st.model_copy(deep=True) if st else None

    def get_by_request(self, request_id: str) -> WorkflowState | None:
        tid = self._by_request.get(request_id)
        return self.get(tid) if tid else None

    def purge_patient(self, patient_ref: str) -> int:
        with self._lock:
            keys = [k for k, v in self._d.items() if v.patient_ref == patient_ref]
            for k in keys:
                del self._d[k]
            return len(keys)


class LongTermMemory:
    def __init__(self, clock: Clock, tenant: str = "demo-tenant", retention_days: int = 365) -> None:
        self.clock, self.tenant, self.retention = clock, tenant, timedelta(days=retention_days)
        self._d: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.legal_hold: set[str] = set()

    def _key(self, patient_ref: str) -> tuple[str, str]:
        return (self.tenant, patient_ref)

    def remember_outcome(self, patient_ref: str, template_id: str, delivery_status: str) -> None:
        self._d.setdefault(self._key(patient_ref), []).append(
            {"kind": "intervention_outcome", "template_id": template_id, "delivery_status": delivery_status,
             "at": self.clock.now(), "source": "delivery-agent", "version": 1}
        )  # fmt: skip

    def recall(self, patient_ref: str, *, consent: Consent | None, purpose: str, now: datetime) -> list[PriorIntervention]:
        """Retrieval filters by authorization (consent), purpose, recency, source trust and version."""
        decision, _, _ = consent_decision(consent, purpose, None, now)
        if decision != "ALLOW":
            return []
        return [
            PriorIntervention(template_id=r["template_id"], sent_at=r["at"], outcome=r["delivery_status"])
            for r in self._d.get(self._key(patient_ref), [])
            if now - r["at"] <= self.retention and r["source"] == "delivery-agent" and r["version"] == 1
        ]

    def raw(self, patient_ref: str) -> list[dict[str, Any]]:
        return list(self._d.get(self._key(patient_ref), []))

    def correct(self, patient_ref: str, idx: int, delivery_status: str) -> None:
        self._d[self._key(patient_ref)][idx]["delivery_status"] = delivery_status

    def delete_patient(self, patient_ref: str) -> dict[str, Any]:
        if patient_ref in self.legal_hold:
            return {"deleted": 0, "blocked": "legal_hold"}
        n = len(self._d.pop(self._key(patient_ref), []))
        return {"deleted": n, "blocked": None}

    def apply_retention(self) -> int:
        now, n = self.clock.now(), 0
        for k, rows in self._d.items():
            keep = [r for r in rows if now - r["at"] <= self.retention]
            n += len(rows) - len(keep)
            self._d[k] = keep
        return n
