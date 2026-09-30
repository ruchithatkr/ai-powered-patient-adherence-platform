"""evaluation-agent: runs offline regression/safety/workflow evaluations and writes signed, reproducible results.

It READS evals/thresholds.yaml (never writes it) and records the file digest, so a threshold change is visible.
Signature = HMAC-SHA256 over a deterministic digest of the metrics (integrity only; NOT a human release approval).
"""

import contextlib
import hashlib
import hmac
import json
import shutil
import subprocess  # nosec B404 - fixed git/pytest invocations only
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agents.supervisor import Supervisor
from app.container import build_runtime
from app.settings import ROOT, Settings
from domain.clock import FixedClock
from domain.models import Status
from guardrails.checks import check_content, check_input
from mcp.gateway import CallIdentity, GatewayError

VERSION = "0.2.0"
EVAL_EPOCH = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)  # fixed clock -> reproducible
DATASET = ROOT / "evals" / "datasets" / "redteam.jsonl"
THRESHOLDS = ROOT / "evals" / "thresholds.yaml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@dataclass
class EvalReport:
    version: str
    metrics: dict[str, Any]
    informational: dict[str, Any]
    failures: list[str]
    provenance: dict[str, Any]
    digest: str = ""
    signature: str | None = None
    failed_cases: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.failures


class EvaluationAgent:
    name = "evaluation-agent"
    version = "1.0.0"

    def __init__(self, results_dir: Path | None = None, signing_key: str | None = None, run_pytest: bool = True) -> None:
        self.results_dir = results_dir or ROOT / "evals" / "results"
        self.signing_key = signing_key
        self.run_pytest = run_pytest

    # ---- scenario runs ---------------------------------------------------------
    async def run(self) -> EvalReport:
        rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
        rt = build_runtime(Settings(audit_path=None), clock=FixedClock(EVAL_EPOCH))
        sup = Supervisor(rt)
        refs = [f"P-00{i}" for i in range(1, 8)]
        t0 = time.perf_counter()
        states = {r: await self._start(sup, r) for r in refs}
        latencies = [(time.perf_counter() - t0) * 1000 / len(refs)]
        sent_before_approval = len(rt.backend.sent)
        for s in [s for s in states.values() if s.status == Status.AWAITING_APPROVAL]:
            await sup.decide(s.trace_id, approver="rph-eval", role="pharmacist", decision="APPROVE", rationale="eval run")

        by_cat: dict[str, dict[str, int]] = {}
        failed: list[str] = []
        bypass = 0
        for row in rows:
            got = await self._classify(sup, rt, row)
            cat = by_cat.setdefault(row["category"], {"n": 0, "ok": 0})
            cat["n"] += 1
            if got == row["expect"]:
                cat["ok"] += 1
            else:
                failed.append(f"{row['id']}: expected {row['expect']} got {got} :: {row['text'][:50]!r}")
            if row["category"] in ("injection", "secret", "phi") and got != "BLOCK":
                bypass += 1 if await self._sent(sup, rt, row) else 0
        recall = {c: round(v["ok"] / v["n"], 4) for c, v in by_cat.items()}
        critical = sum(
            by_cat[c]["n"] - by_cat[c]["ok"] for c in ("injection", "secret", "phi", "emergency", "unsafe_content") if c in by_cat
        )
        # retry safety / idempotency / tool-failure recovery
        retry_safe = await self._retry_safety()
        gaps = sum(bool(rt.audit.gaps(s.trace_id)) for s in states.values())
        schema_valid = all(type(s).model_validate(s.model_dump()) for s in states.values())
        completion = sum(
            s.status in (Status.COMPLETED, Status.ESCALATED, Status.AWAITING_APPROVAL, Status.STOPPED) for s in states.values()
        )
        metrics: dict[str, Any] = {
            "critical_safety_violations": critical,
            "unauthorized_writes": sent_before_approval,
            "approval_bypass_rate": bypass,
            "trace_gaps": gaps,
            "audit_chain_valid": rt.audit.verify_chain(),
            "detector_recall_by_category": recall,
            "benign_false_positive_rate": round(1 - recall.get("benign", 1.0), 4),
            "emergency_escalation_recall": recall.get("emergency"),
            "dosage_change_leakage": by_cat.get("unsafe_content", {"n": 0, "ok": 0})["n"]
            - by_cat.get("unsafe_content", {"ok": 0})["ok"],
            "workflow_schema_validity": schema_valid,
            "workflow_terminal_or_waiting_rate": round(completion / len(states), 4),
            "idempotent_duplicate_send_prevented": retry_safe["idempotent"],
            "no_blind_retry_on_write_failure": retry_safe["no_retry"],
        }
        gates = yaml.safe_load(THRESHOLDS.read_text())["release_gates"]
        failures = [
            f"{k}={metrics[k]} > {gates[k]['max']}"
            for k in ("critical_safety_violations", "unauthorized_writes", "approval_bypass_rate", "trace_gaps")
            if metrics[k] > gates[k]["max"]
        ]
        if not metrics["audit_chain_valid"]:
            failures.append("audit chain invalid")
        if not (retry_safe["idempotent"] and retry_safe["no_retry"]):
            failures.append("retry-safety check failed")
        prov = {
            "dataset": str(DATASET.relative_to(ROOT)),
            "dataset_digest": _sha(DATASET),
            "dataset_rows": len(rows),
            "thresholds_digest": _sha(THRESHOLDS),
            "policy": rt.policy.policy_version,
            "policy_digest": rt.policy.digest,
            "templates": rt.templates.version,
            "workflow_version": rt.policy.workflow_version,
            "risk_model": "adherence-risk-deterministic-fallback@0.1.0",
            "llm": "none",
            "prompts": "none",
            "agent": f"{self.name}@{self.version}",
            "eval_epoch": EVAL_EPOCH.isoformat(),
        }
        info = {
            "mean_workflow_latency_ms": round(sum(latencies) / len(latencies), 2),
            "tests": self._pytest_line(),
            "commit": self._commit(),
            "run_date_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        report = EvalReport(VERSION, metrics, info, failures, prov, failed_cases=failed)
        payload = json.dumps({"v": VERSION, "metrics": metrics, "prov": prov}, sort_keys=True)
        report.digest = hashlib.sha256(payload.encode()).hexdigest()
        if self.signing_key:
            report.signature = hmac.new(self.signing_key.encode(), report.digest.encode(), hashlib.sha256).hexdigest()
        return report

    async def _start(self, sup: Supervisor, ref: str, msg: str = "") -> Any:
        return await sup.start(
            patient_ref=ref,
            purpose="adherence_support",
            requester="coord-eval",
            requester_role="care_coordinator",
            identity_patient_ref=ref,
            patient_message=msg,
        )

    async def _classify(self, sup: Supervisor, rt: Any, row: dict[str, str]) -> str:
        cat, text = row["category"], row["text"]
        if cat in ("unsafe_content", "safe_content"):
            return check_content(text).decision
        results = check_input(text)
        st = await self._start(sup, "P-003", text)
        if any(g.decision == "BLOCK" for g in results):
            return "BLOCK" if st.stop_reason == "INPUT_BLOCKED" else "MISSED"
        if any(g.decision == "REVIEW" for g in results):
            return "REVIEW" if st.status == Status.ESCALATED and st.review_task_id else "MISSED"
        return "PASS" if st.stop_reason not in ("INPUT_BLOCKED", "SAFETY_ESCALATION") else "FALSE_POSITIVE"

    async def _sent(self, sup: Supervisor, rt: Any, row: dict[str, str]) -> bool:
        before = len(rt.backend.sent)
        await self._start(sup, "P-003", row["text"])
        return len(rt.backend.sent) > before

    async def _retry_safety(self) -> dict[str, bool]:
        rt = build_runtime(Settings(audit_path=None), clock=FixedClock(EVAL_EPOCH))
        sup = Supervisor(rt)
        st = await self._start(sup, "P-003")
        plan = st.intervention_plan
        ident = CallIdentity("coord-eval", "care_coordinator", "adherence_support", st.trace_id)
        token = f"APR-eval-{uuid.uuid4().hex[:8]}"
        rt.approvals.grant(token, plan.idempotency_key)
        params = {
            "idempotency_key": plan.idempotency_key,
            "channel": plan.channel,
            "template_id": plan.template_id,
            "body": plan.body,
            "patient_ref": "P-003",
        }
        a = await rt.gateway.call("notification-send", "send", params, ident=ident, approval_token=token)
        b = await rt.gateway.call("notification-send", "send", params, ident=ident, approval_token=token)
        idem = b["duplicate"] and a["receipt_id"] == b["receipt_id"] and len(rt.backend.sent) == 1
        rt2 = build_runtime(Settings(audit_path=None), clock=FixedClock(EVAL_EPOCH))
        sup2 = Supervisor(rt2)
        st2 = await self._start(sup2, "P-003")
        rt2.backend.fail.add("notification-send")
        done = await sup2.decide(st2.trace_id, approver="rph-eval", role="pharmacist", decision="APPROVE", rationale="eval")
        calls = [e for e in rt2.audit.trace(st2.trace_id) if e["node"] == "tool:notification-send.send"]
        with contextlib.suppress(GatewayError):
            await rt2.gateway.call("notification-send", "send", params, ident=ident, approval_token=None)
        return {"idempotent": bool(idem), "no_retry": done.status == Status.STOPPED and len(calls) == 1 and not rt2.backend.sent}

    # ---- helpers / outputs -----------------------------------------------------
    def _commit(self) -> str:
        git = shutil.which("git")
        if git is None:
            return "none"
        try:
            sha = subprocess.run(  # nosec B603 - fixed argv, no untrusted input
                [git, "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=False
            ).stdout.strip()  # noqa: S603
            dirty = subprocess.run(  # nosec B603 - fixed argv, no untrusted input
                [git, "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=False
            ).stdout.strip()  # noqa: S603
            return f"{sha or 'none'}{'+uncommitted-changes' if dirty else ''}"
        except (OSError, subprocess.SubprocessError):
            return "none"

    def _pytest_line(self) -> str:
        if not self.run_pytest:
            return "not run"
        r = subprocess.run(  # nosec B603 - fixed argv, no untrusted input
            [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider", "--ignore=tests/test_evals.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )  # noqa: S603
        return next((ln for ln in reversed(r.stdout.splitlines()) if " passed" in ln or " failed" in ln), "(no summary)")

    def write(self, report: EvalReport) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        m, p = report.metrics, report.provenance
        recall = m["detector_recall_by_category"]
        md = f"""# Evaluation results {report.version}
- **Dataset / provenance**: `{p["dataset"]}` ({p["dataset_rows"]} rows, digest `{p["dataset_digest"]}`) + 7 synthetic patients (`domain/synthetic.py`). Hand-written, tuned during development, NOT held-out, NOT clinically reviewed.
- **Run date (UTC)**: {report.informational["run_date_utc"]}
- **Commit**: {report.informational["commit"]}
- **Versions**: policy {p["policy"]} (`{p["policy_digest"]}`), templates {p["templates"]}, workflow {p["workflow_version"]}, risk model {p["risk_model"]}, LLM: {p["llm"]}, prompts: {p["prompts"]}, agent {p["agent"]}
- **Thresholds file digest**: `{p["thresholds_digest"]}` (read-only for this agent)
- **Result digest**: `{report.digest}` - **Signature**: {report.signature or "none (no ADHERENCE_EVAL_SIGNING_KEY configured)"}

## Metrics (executed)
| Metric | Value | Gate |
|---|---|---|
| Critical safety-policy violations | {m["critical_safety_violations"]} | max 0 |
| Unauthorized external writes (send before approval) | {m["unauthorized_writes"]} | max 0 |
| Approval bypass via adversarial input | {m["approval_bypass_rate"]} | max 0 |
| Trace gaps (7 workflows) | {m["trace_gaps"]} | max 0 |
| Audit hash chain valid | {m["audit_chain_valid"]} | required |
| Emergency escalation recall (dev set) | {m["emergency_escalation_recall"]} | - |
| Dosage-change/unsafe wording leakage (count) | {m["dosage_change_leakage"]} | - |
| Benign false-positive rate (dev set) | {m["benign_false_positive_rate"]} | - |
| Workflow schema validity | {m["workflow_schema_validity"]} | - |
| Duplicate send prevented (idempotency) | {m["idempotent_duplicate_send_prevented"]} | - |
| No blind retry after write failure | {m["no_blind_retry_on_write_failure"]} | - |
| Test suite | {report.informational["tests"]} | - |

Detector recall by category (dev set): {json.dumps(recall)}
Mean workflow latency (informational, wall-clock): {report.informational["mean_workflow_latency_ms"]} ms

## NOT YET MEASURED
AUROC/AUPRC, sensitivity/specificity, calibration error, Brier score, subgroup/equity metrics, engagement, opt-out and override rates
in production, drift (no approved baseline), p95 latency under load, cost. No labelled or representative dataset exists.
Scorers are implemented and unit-tested in `evals/scorers.py` and ready for an approved dataset.

## Failed cases and root cause
{chr(10).join("- " + f for f in report.failed_cases + report.failures) or "None on this dev set (note: patterns were tuned against it)."}

## Known limitations
Small hand-authored dev set; keyword/regex detectors can miss paraphrases and other languages; delivery is simulated; no load testing.

## Reviewer / release decision
**UNSIGNED - no human reviewer has approved a release.** A passing run is not a release approval.
"""
        out = self.results_dir / f"{report.version}.md"
        out.write_text(md)
        (self.results_dir / f"{report.version}.json").write_text(
            json.dumps(
                {
                    "digest": report.digest,
                    "signature": report.signature,
                    "metrics": report.metrics,
                    "provenance": report.provenance,
                },
                indent=1,
                sort_keys=True,
            )
        )
        return out
