"""Namespaced long-term memory + short-lived workflow state.

Long-term memory keeps only consented preferences, approved care-plan facts and intervention outcomes.
No raw chain-of-thought, no raw prompts, no patient free text.
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from domain.models import WorkflowState


class ConflictError(Exception):
    pass


class WorkflowStore:
    """Short-term workflow state with TTL and optimistic locking (encrypted store in production)."""

    def __init__(self, ttl_hours: int = 24):
        self._d: dict[str, WorkflowState] = {}
        self._lock = threading.Lock()
        self.ttl = timedelta(hours=ttl_hours)

    def put(self, st: WorkflowState, expected_version: Optional[int] = None) -> WorkflowState:
        with self._lock:
            cur = self._d.get(st.trace_id)
            if cur is not None and expected_version is not None and cur.version != expected_version:
                raise ConflictError("workflow modified concurrently")
            st.version = (cur.version + 1) if cur else 0
            st.expires_at = datetime.now(timezone.utc) + self.ttl
            self._d[st.trace_id] = st.model_copy(deep=True)
            return st

    def get(self, trace_id: str) -> Optional[WorkflowState]:
        with self._lock:
            st = self._d.get(trace_id)
            if st and st.expires_at and st.expires_at < datetime.now(timezone.utc):
                del self._d[trace_id]
                return None
            return st.model_copy(deep=True) if st else None

    def purge_patient(self, patient_ref: str) -> int:
        with self._lock:
            keys = [k for k, v in self._d.items() if v.patient_ref == patient_ref]
            for k in keys:
                del self._d[k]
            return len(keys)


class LongTermMemory:
    def __init__(self, tenant: str = "demo-tenant", retention_days: int = 365):
        self.tenant, self.retention = tenant, timedelta(days=retention_days)
        self._d: dict[tuple[str, str], list[dict]] = {}
        self.legal_hold: set[str] = set()

    def _key(self, patient_ref):
        return (self.tenant, patient_ref)

    def remember_outcome(self, patient_ref: str, template_id: str, delivery_status: str):
        self._d.setdefault(self._key(patient_ref), []).append(
            {"kind": "intervention_outcome", "template_id": template_id, "delivery_status": delivery_status,
             "at": datetime.now(timezone.utc)})

    def recall(self, patient_ref: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [r for r in self._d.get(self._key(patient_ref), []) if now - r["at"] <= self.retention]

    def correct(self, patient_ref: str, idx: int, delivery_status: str):
        self._d[self._key(patient_ref)][idx]["delivery_status"] = delivery_status

    def delete_patient(self, patient_ref: str) -> dict:
        if patient_ref in self.legal_hold:
            return {"deleted": 0, "blocked": "legal_hold"}
        n = len(self._d.pop(self._key(patient_ref), []))
        return {"deleted": n, "blocked": None}

    def apply_retention(self) -> int:
        now, n = datetime.now(timezone.utc), 0
        for k, rows in self._d.items():
            keep = [r for r in rows if now - r["at"] <= self.retention]
            n += len(rows) - len(keep)
            self._d[k] = keep
        return n
