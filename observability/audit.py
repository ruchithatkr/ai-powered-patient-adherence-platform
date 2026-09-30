"""Append-only, hash-chained audit store. PHI-safe: stores hashes of inputs/outputs, never raw text."""
import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

FORBIDDEN_KEYS = {"prompt", "patient_text", "token", "access_token", "name", "email", "phone", "body"}


def h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class AuditStore:
    """Agents get append() only; there is no update/delete API."""

    def __init__(self, path: Optional[Path] = None):
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._path = path
        self._prev = "GENESIS"

    def append(self, *, trace_id: str, actor: str, node: str, status: str = "OK", parent_id: Optional[str] = None,
               input: Any = None, output: Any = None, **fields) -> str:
        bad = FORBIDDEN_KEYS & set(fields)
        if bad:
            raise ValueError(f"PHI-unsafe audit fields refused: {sorted(bad)}")
        with self._lock:
            ev = {"event_id": uuid.uuid4().hex[:12], "timestamp": datetime.now(timezone.utc).isoformat(),
                  "trace_id": trace_id, "actor": actor, "node": node, "status": status, "parent_id": parent_id,
                  "input_hash": h(input) if input is not None else None,
                  "output_hash": h(output) if output is not None else None, **fields, "prev_hash": self._prev}
            ev["hash"] = h(ev)
            self._prev = ev["hash"]
            self._events.append(ev)
            if self._path:
                with self._path.open("a") as f:
                    f.write(json.dumps(ev, default=str) + "\n")
            return ev["event_id"]

    def trace(self, trace_id: str) -> list[dict]:
        return [e for e in self._events if e["trace_id"] == trace_id]

    def all(self) -> list[dict]:
        return list(self._events)

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for e in self._events:
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev_hash"] != prev or h(body) != e["hash"]:
                return False
            prev = e["hash"]
        return True

    def export_redacted(self, trace_id: str) -> list[dict]:
        return [{k: v for k, v in e.items() if k not in ("prev_hash",)} for e in self.trace(trace_id)]

    def completeness(self, trace_id: str, required: list[str]) -> list[str]:
        """Return the required nodes missing from a trace (trace-gap check)."""
        seen = {e["node"] for e in self.trace(trace_id)}
        return [n for n in required if n not in seen]
