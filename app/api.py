"""FastAPI entry point (app factory + dependency injection). Run with: uvicorn app.api:create_app --factory"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agents.supervisor import Supervisor
from app.container import build_runtime
from app.deps import REVIEWERS, Principal, get_principal, get_runtime, get_supervisor
from mcp.gateway import CallIdentity, GatewayError
from memory.store import ConflictError
from observability.audit import chain_coverage
from observability.logging import configure_logging
from workflows.runtime import Runtime

log = logging.getLogger("adherence.access")


class StartReq(BaseModel):
    patient_ref: str = Field(pattern=r"^P-[0-9]{3,}$")
    identity_patient_ref: str
    purpose: str = "adherence_support"
    patient_message: str = Field("", max_length=2000)


class DecisionReq(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    rationale: str
    conditions: str = ""
    expected_version: int | None = None


class CorrectionReq(BaseModel):
    delivery_status: str = Field(min_length=1, max_length=64)


def _view(rt: Runtime, st: Any) -> dict[str, Any]:
    d: dict[str, Any] = st.model_dump(mode="json")
    if d.get("intervention_plan"):
        d["intervention_plan"].pop("template_fields", None)
    events = rt.audit.trace(st.trace_id)
    d["trace_gaps"] = rt.audit.gaps(st.trace_id)
    d["trace_complete"] = not d["trace_gaps"]
    d["chain_links"] = chain_coverage(events)
    return d


def _require(who: Principal, *roles: str) -> None:
    if who.role not in roles:
        raise HTTPException(403, f"{'/'.join(roles)} only")


def create_app(runtime: Runtime | None = None) -> FastAPI:
    rt = runtime or build_runtime()
    configure_logging(rt.settings.log_level)
    app = FastAPI(title="AI-Powered Patient Adherence Platform", version="1.1.0")
    app.state.runtime = rt
    app.state.supervisor = Supervisor(rt)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or "rq-" + uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        rt.metrics.inc("adherence_http_requests_total", method=request.method, route=route, status=str(response.status_code))
        rt.metrics.observe("adherence_http_latency_ms", ms, route=route)
        response.headers["X-Request-ID"] = rid
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": route,
                "status_code": response.status_code,
                "latency_ms": ms,
            },
        )
        return response

    # ---- health / ops ----------------------------------------------------------
    @app.get("/health")
    def health(rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
        return {"status": "ok", "synthetic_data_only": True, "audit_chain_valid": rt.audit.verify_chain(incremental=True)}

    @app.get("/ready")
    def ready(rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
        blocking = [a.rule for a in rt.alerts.blocking()]
        ok = rt.audit.verify_chain(incremental=True) and not blocking
        if not ok:
            raise HTTPException(503, {"ready": False, "blocking_alerts": blocking})
        return {"ready": True, "policy_version": rt.policy.policy_version, "policy_digest": rt.policy.digest}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(rt: Runtime = Depends(get_runtime)) -> str:
        gaps = sum(1 for t in rt.audit.open_traces() if rt.audit.gaps(t) and any(e["node"] == "final" for e in rt.audit.trace(t)))
        return rt.metrics.prometheus({"adherence_trace_gaps": gaps, "adherence_audit_events": len(rt.audit)})

    @app.get("/ops/summary")
    def ops_summary(_: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
        m = rt.metrics
        total = m.counter("adherence_workflows_total")
        tool_total = m.counter("adherence_tool_calls_total")
        guard_total = m.counter("adherence_guardrail_decisions_total")
        band_total = m.counter("adherence_risk_band_total")
        human = m.counter("adherence_approvals_total", kind="human")

        def rate(n: float, d: float) -> float | None:
            return round(n / d, 4) if d else None

        alerts = rt.alerts.evaluate()
        snap = m.snapshot()
        return {
            "workflows": {
                s: m.counter("adherence_workflows_total", status=s)
                for s in ("COMPLETED", "STOPPED", "ESCALATED", "AWAITING_APPROVAL", "REJECTED")
            },
            "success_rate": rate(m.counter("adherence_workflows_total", status="COMPLETED"), total),
            "tool_error_rate": rate(tool_total - m.counter("adherence_tool_calls_total", status="ok"), tool_total),
            "block_rate": rate(m.counter("adherence_guardrail_decisions_total", decision="BLOCK"), guard_total),
            "abstain_rate": rate(m.counter("adherence_risk_band_total", band="ABSTAIN"), band_total),
            "override_rate": rate(m.counter("adherence_approvals_total", decision="REJECT", kind="human"), human),
            "latency_ms": snap["histograms"].get("adherence_tool_latency_ms", []),
            "tokens_and_cost": {
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "note": "no LLM in decision path",
            },
            "drift_psi": rt.alerts.drift(),
            "drift_note": None if rt.alerts.drift_baseline else "no approved baseline configured; drift NOT MEASURED",
            "trace_gaps": [a.detail for a in alerts if a.rule == "MISSING_AUDIT_EVENTS"],
            "alerts": [a.__dict__ for a in alerts],
            "audit": {"events": len(rt.audit), "chain_valid": rt.audit.verify_chain(incremental=True)},
        }

    @app.get("/alerts")
    def alerts(_: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> list[dict[str, Any]]:
        return [a.__dict__ for a in rt.alerts.evaluate_and_record()]

    # ---- workflows ---------------------------------------------------------------
    @app.get("/patients")
    def patients(_: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> list[dict[str, str]]:
        return [{"patient_ref": k} for k in rt.backend.db]

    @app.post("/workflows")
    async def start(
        req: StartReq,
        who: Principal = Depends(get_principal),
        sup: Supervisor = Depends(get_supervisor),
        rt: Runtime = Depends(get_runtime),
        idempotency_key: str | None = Header(None),
    ) -> dict[str, Any]:
        st = await sup.start(
            patient_ref=req.patient_ref, purpose=req.purpose, requester=who.actor, requester_role=who.role,
            identity_patient_ref=req.identity_patient_ref, patient_message=req.patient_message, request_id=idempotency_key,
        )  # fmt: skip
        return _view(rt, st)

    @app.get("/workflows/{trace_id}")
    def get_workflow(trace_id: str, _: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
        st = rt.store.get(trace_id)
        if not st:
            raise HTTPException(404, "not found or expired")
        return _view(rt, st)

    @app.post("/workflows/{trace_id}/decision")
    async def decide(
        trace_id: str, req: DecisionReq, who: Principal = Depends(get_principal),
        sup: Supervisor = Depends(get_supervisor), rt: Runtime = Depends(get_runtime),
    ) -> dict[str, Any]:  # fmt: skip
        try:
            st = await sup.decide(
                trace_id, approver=who.actor, role=who.role, decision=req.decision, rationale=req.rationale,
                conditions=req.conditions, expected_version=req.expected_version,
            )  # fmt: skip
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ConflictError as e:
            raise HTTPException(409, str(e)) from e
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return _view(rt, st)

    @app.get("/workflows/{trace_id}/trace")
    def trace(trace_id: str, _: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> list[dict[str, Any]]:
        return rt.audit.export_redacted(trace_id)

    @app.get("/workflows/{trace_id}/explanation")
    def explanation(
        trace_id: str, _: Principal = Depends(get_principal), sup: Supervisor = Depends(get_supervisor)
    ) -> dict[str, Any]:
        try:
            return sup.explain(trace_id)
        except KeyError as e:
            raise HTTPException(404, "not found") from e

    # ---- human review tasks -----------------------------------------------------
    @app.get("/review-tasks")
    def review_tasks(who: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> list[dict[str, Any]]:
        _require(who, *REVIEWERS)
        return list(rt.backend.tasks.values())

    @app.get("/review-tasks/{task_id}")
    async def review_task(task_id: str, who: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> Any:
        _require(who, *REVIEWERS)
        try:
            ident = CallIdentity(who.actor, who.role, "safety_review", "ops-read")
            return await rt.gateway.call("care-team-review", "get_task", {"task_id": task_id}, ident=ident)
        except GatewayError as e:
            raise HTTPException(404 if e.code in ("NOT_FOUND", "SCHEMA_INVALID") else 502, e.code) from e

    # ---- patient rights ---------------------------------------------------------
    @app.post("/patients/{patient_ref}/withdraw-consent")
    def withdraw(
        patient_ref: str,
        who: Principal = Depends(get_principal),
        sup: Supervisor = Depends(get_supervisor),
        rt: Runtime = Depends(get_runtime),
    ) -> dict[str, str]:
        if patient_ref not in rt.backend.db:
            raise HTTPException(404, "unknown patient")
        sup.withdraw_consent(patient_ref, who.actor)
        return {"patient_ref": patient_ref, "consent": "withdrawn"}

    @app.delete("/patients/{patient_ref}/data")
    def delete(
        patient_ref: str, who: Principal = Depends(get_principal), sup: Supervisor = Depends(get_supervisor)
    ) -> dict[str, Any]:
        _require(who, "privacy_officer")
        return sup.delete_patient(patient_ref, who.actor)

    @app.get("/patients/{patient_ref}/data-export")
    def export(
        patient_ref: str, who: Principal = Depends(get_principal), sup: Supervisor = Depends(get_supervisor)
    ) -> dict[str, Any]:
        _require(who, "privacy_officer")
        return sup.access_export(patient_ref, who.actor)

    @app.patch("/patients/{patient_ref}/memory/{idx}")
    def correct(
        patient_ref: str,
        idx: int,
        req: CorrectionReq,
        who: Principal = Depends(get_principal),
        sup: Supervisor = Depends(get_supervisor),
    ) -> dict[str, str]:
        _require(who, "privacy_officer")
        try:
            sup.correct_outcome(patient_ref, idx, req.delivery_status, who.actor)
        except (KeyError, IndexError) as e:
            raise HTTPException(404, "no such record") from e
        return {"status": "corrected"}

    @app.post("/admin/retention")
    def retention(who: Principal = Depends(get_principal), sup: Supervisor = Depends(get_supervisor)) -> dict[str, int]:
        _require(who, "privacy_officer")
        return {"removed": sup.apply_retention(who.actor)}

    @app.get("/audit/verify")
    def verify(_: Principal = Depends(get_principal), rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
        return {"chain_valid": rt.audit.verify_chain(), "events": len(rt.audit)}

    return app
