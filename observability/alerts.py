"""Alert rules evaluated over the audit trail. Alerts are recorded as audit events (deduplicated)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from domain.clock import Clock
from observability.audit import AuditStore
from observability.drift import psi
from workflows.policy import Policy

UNAUTHORIZED_CODES = {
    "ROLE_NOT_AUTHORIZED", "APPROVAL_REQUIRED", "SERVER_NOT_ALLOWLISTED", "OP_NOT_ALLOWED", "PURPOSE_NOT_ALLOWED",
    "CONSENT_NOT_ACTIVE", "APPROVER_ROLE", "SEGREGATION_OF_DUTIES", "DESTINATION_NOT_ALLOWED",
}  # fmt: skip
SAFETY_CODES = {"INPUT_BLOCKED", "CONTENT_BLOCKED", "SAFETY_ESCALATION", "UNTRUSTED_CONTENT"}


@dataclass(frozen=True)
class Alert:
    key: str
    rule: str
    severity: str
    detail: str
    blocking: bool = False


class AlertEngine:
    def __init__(self, audit: AuditStore, policy: Policy, clock: Clock, drift_baseline: dict[str, float] | None = None) -> None:
        self.audit, self.policy, self.clock = audit, policy, clock
        self.drift_baseline = drift_baseline
        self._recorded: set[str] = set()

    def _recent(self, minutes: int) -> list[dict[str, Any]]:
        cutoff = (self.clock.now() - timedelta(minutes=minutes)).isoformat()
        return [e for e in self.audit.all() if e["timestamp"] >= cutoff and e["node"] != "alert"]

    def evaluate(self) -> list[Alert]:
        alerts: list[Alert] = []
        events = self.audit.all()
        real = [e for e in events if e["node"] != "alert"]
        for e in real:
            if e["status"] == "DENIED" and e.get("error_code") in UNAUTHORIZED_CODES:
                alerts.append(
                    Alert(f"unauth:{e['span_id']}", "UNAUTHORIZED_ATTEMPT", "high", f"{e.get('error_code')} by {e['actor']}")
                )
            if e.get("error_code") in SAFETY_CODES or e.get("guardrail_decision") == "BLOCK":
                alerts.append(Alert(f"safety:{e['span_id']}", "SAFETY_BLOCK", "medium", f"{e['node']} {e.get('error_code')}"))
        cfg = self.policy.alerts.repeated_tool_failures
        fails: dict[str, int] = {}
        for e in self._recent(cfg["window_minutes"]):
            if e["status"] == "ERROR" and e.get("tool"):
                fails[e["tool"]] = fails.get(e["tool"], 0) + 1
        for tool, n in fails.items():
            if n >= cfg["threshold"]:
                alerts.append(Alert(f"fail:{tool}:{n}", "REPEATED_TOOL_FAILURE", "high", f"{tool} failed {n}x"))
        acfg = self.policy.alerts.anomalous_access
        by_actor: dict[str, set[str]] = {}
        for e in self._recent(acfg["window_minutes"]):
            if e["node"] == "request" and e.get("subject_hash"):
                by_actor.setdefault(e["actor"], set()).add(e["subject_hash"])
        for actor, subjects in by_actor.items():
            if len(subjects) >= acfg["distinct_patients"]:
                alerts.append(
                    Alert(
                        f"access:{actor}:{len(subjects)}", "ANOMALOUS_ACCESS", "high", f"{actor} touched {len(subjects)} patients"
                    )
                )
        for trace_id in self.audit.open_traces():
            if (missing := self.audit.gaps(trace_id)) and any(e["node"] == "final" for e in self.audit.trace(trace_id)):
                alerts.append(Alert(f"gap:{trace_id}", "MISSING_AUDIT_EVENTS", "critical", f"{trace_id} missing {missing}", True))
        if not self.audit.verify_chain(incremental=True):
            alerts.append(Alert("chain", "MISSING_AUDIT_EVENTS", "critical", "audit hash chain invalid", True))
        drift = self.drift()
        if drift is not None and drift > self.policy.alerts.drift_psi:
            alerts.append(Alert(f"drift:{round(drift, 2)}", "DRIFT", "medium", f"risk-band PSI {drift:.2f}"))
        return alerts

    def drift(self) -> float | None:
        if not self.drift_baseline:
            return None  # no approved baseline configured -> not measured
        cur: dict[str, float] = {}
        for e in self.audit.all():
            if e["node"] == "stage:assess" and e["status"] == "OK" and e.get("band"):
                cur[e["band"]] = cur.get(e["band"], 0) + 1
        return psi(self.drift_baseline, cur) if cur else None

    def evaluate_and_record(self, trace_id: str = "ops") -> list[Alert]:
        alerts = self.evaluate()
        for a in alerts:
            if a.key not in self._recorded:
                self._recorded.add(a.key)
                self.audit.append(
                    trace_id=trace_id, actor="alert-engine", node="alert", status="ALERT",
                    error_code=a.rule, alert_key=a.key, severity=a.severity, detail=a.detail,
                )  # fmt: skip
        return alerts

    def blocking(self) -> list[Alert]:
        return [a for a in self.evaluate() if a.blocking]

    @staticmethod
    def window_start(clock: Clock, minutes: int) -> datetime:
        return clock.now() - timedelta(minutes=minutes)
