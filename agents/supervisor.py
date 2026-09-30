"""adherence-supervisor: owns workflow state and the state-machine engine.

Deterministic rules decide permissions, consent, emergency signals and approval gates. Stage logic lives in
workflows/stages.py; this module drives transitions, persistence, tracing and patient-rights operations.
"""

import asyncio
import uuid
from typing import Any, Literal

from domain.errors import WorkflowStop
from domain.models import Status, WorkflowState
from mcp.gateway import CallIdentity, GatewayError
from memory.store import ConflictError
from observability.audit import h
from workflows.definition import (
    APPROVER_ROLES,
    DENIED_STOP_REASONS,
    NEXT,
    Continue,
    Finish,
    Outcome,
    Scratch,
    Stop,
    Wait,
    require,
)
from workflows.runtime import Runtime
from workflows.stages import HANDLERS

AGENT_VERSION = "1.1.0"


class Supervisor:
    name = "adherence-supervisor"

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    # ---- entry point ----------------------------------------------------------
    async def start(
        self,
        *,
        patient_ref: str,
        purpose: str,
        requester: str,
        requester_role: str,
        identity_patient_ref: str,
        patient_message: str = "",
        request_id: str | None = None,
    ) -> WorkflowState:
        rt = self.rt
        if request_id and (existing := rt.store.get_by_request(request_id)):
            return existing  # duplicate submission: idempotent
        st = WorkflowState(
            trace_id="tr-" + uuid.uuid4().hex[:12], request_id=request_id or "rq-" + uuid.uuid4().hex[:8],
            patient_ref=patient_ref, purpose=purpose, requester=requester, requester_role=requester_role,
        )  # fmt: skip
        ident = CallIdentity(requester, requester_role, purpose, st.trace_id)
        scratch = Scratch(identity_patient_ref=identity_patient_ref, patient_message=patient_message)
        with rt.tracer.bind(
            request_id=st.request_id, workflow_version=rt.policy.workflow_version, policy_version=rt.policy.policy_version
        ):
            async with rt.tracer.span(
                trace_id=st.trace_id, node="request", actor=requester, subject_hash=h(patient_ref),
                purpose=purpose, role=requester_role, input={"patient_ref": patient_ref, "purpose": purpose, "role": requester_role},
            ) as root:  # fmt: skip
                st.root_span_id = root.span_id
                rt.tracer.event(
                    trace_id=st.trace_id, node="agent_plan", actor=self.name, agent=self.name, agent_version=AGENT_VERSION,
                    output=["consent-access", "patient-context", "adherence-risk", "intervention-planner", "content-safety", "human-review", "delivery"],
                )  # fmt: skip
                await self._drive(st, ident, scratch, "intake", expected_version=None)
        return st

    # ---- engine ---------------------------------------------------------------
    async def _drive(
        self, st: WorkflowState, ident: CallIdentity, scratch: Scratch, stage: str, expected_version: int | None
    ) -> None:
        rt = self.rt
        while stage != "close":
            st.stage = stage
            if stage not in st.stages_visited:
                st.stages_visited.append(stage)
            outcome = await self._run_stage(st, ident, scratch, stage)
            if isinstance(outcome, Continue):
                stage = NEXT[stage]
            elif isinstance(outcome, Wait):
                st.status = Status.AWAITING_APPROVAL
                rt.tracer.event(
                    trace_id=st.trace_id, node="approval_requested", actor=self.name, status="PENDING",
                    approval_id=st.review_task_id, stages_visited=list(st.stages_visited),
                )  # fmt: skip
                rt.store.put(st, expected_version)
                return
            else:
                self._finalize(st, outcome)
                break
        await self._close(st, expected_version)

    async def _run_stage(self, st: WorkflowState, ident: CallIdentity, scratch: Scratch, stage: str) -> Outcome:
        rt = self.rt
        async with rt.tracer.span(
            trace_id=st.trace_id, node=f"stage:{stage}", actor=self.name, agent=self.name, agent_version=AGENT_VERSION
        ) as sp:
            try:
                outcome = await HANDLERS[stage].run(rt, st, ident, scratch, sp)
            except WorkflowStop as e:
                outcome = Stop(e.reason, e.status, e.rationale)
            except GatewayError as e:
                outcome = Stop(e.code)
            if isinstance(outcome, Stop):
                sp.status = (
                    "DENIED"
                    if outcome.reason in DENIED_STOP_REASONS
                    or outcome.reason.startswith("CONSENT_")
                    and outcome.reason != "CONSENT_REVIEW"
                    else "STOPPED"
                )
                sp.set(error_code=outcome.reason)
            elif isinstance(outcome, Wait):
                sp.status = "PENDING"
            return outcome

    def _finalize(self, st: WorkflowState, outcome: Outcome) -> None:
        if isinstance(outcome, Stop):
            st.status, st.stop_reason = outcome.status, outcome.reason
            st.rationale = outcome.rationale or f"Workflow stopped: {outcome.reason}."
            self.rt.tracer.event(trace_id=st.trace_id, node="stop", actor=self.name, status="STOPPED", error_code=outcome.reason)
        elif isinstance(outcome, Finish):
            st.status, st.rationale = outcome.status, outcome.rationale

    async def _close(self, st: WorkflowState, expected_version: int | None) -> None:
        rt = self.rt
        st.stage = "close"
        if "close" not in st.stages_visited:
            st.stages_visited.append("close")
        rt.tracer.event(
            trace_id=st.trace_id, node="final", actor=self.name, status=st.status.value,
            output={"status": st.status.value, "stop": st.stop_reason}, stages_visited=list(st.stages_visited),
            evidence_ids=st.evidence_refs, approval_id=st.approval.approval_id if st.approval else None,
            action_receipt=st.action_receipt.receipt_id if st.action_receipt else None,
            error_code=st.stop_reason, guardrail_decision=("BLOCK" if st.stop_reason in ("CONTENT_BLOCKED", "INPUT_BLOCKED") else None),
        )  # fmt: skip
        rt.metrics.inc("adherence_workflows_total", status=st.status.value)
        if st.stop_reason:
            rt.metrics.inc("adherence_workflow_stops_total", reason=st.stop_reason)
        rt.store.put(st, expected_version)
        rt.alerts.evaluate_and_record(st.trace_id)

    # ---- human decision -------------------------------------------------------
    async def decide(
        self, trace_id: str, *, approver: str, role: str, decision: Literal["APPROVE", "REJECT"], rationale: str, conditions: str = "",
        expected_version: int | None = None,
    ) -> WorkflowState:  # fmt: skip
        rt = self.rt
        lock = rt.locks.setdefault(trace_id, asyncio.Lock())
        async with lock:  # one decision at a time per workflow
            st = rt.store.get(trace_id)
            if not st:
                raise KeyError("workflow not found or expired")
            loaded = st.version
            ident = CallIdentity(st.requester, st.requester_role, st.purpose, st.trace_id)
            with rt.tracer.bind(
                request_id=st.request_id, workflow_version=rt.policy.workflow_version, policy_version=rt.policy.policy_version
            ):
                async with rt.tracer.span(
                    trace_id=trace_id, node="decision", actor=approver, parent_span_id=st.root_span_id, role=role
                ) as sp:
                    self._authorize_decision(st, approver, role, rationale, expected_version, sp)
                    plan = require(st.intervention_plan, "intervention plan")
                    if plan.expires_at < rt.clock.now():
                        self._finalize(st, Stop("PLAN_EXPIRED"))
                        await self._close(st, loaded)
                        return st
                    # Re-check consent at execution time (withdrawal may have occurred while waiting).
                    res = await rt.consent_agent.run(ident, st.patient_ref, plan.channel)
                    if res.decision != "ALLOW":
                        self._finalize(
                            st,
                            Stop(
                                f"CONSENT_{res.decision}",
                                rationale=f"Consent changed while awaiting approval: {res.reason}. No message sent.",
                            ),
                        )
                        await self._close(st, loaded)
                        return st
                    st.approval = rt.reviewer.record(approver, role, decision, rationale, conditions)
                    rt.metrics.inc("adherence_approvals_total", decision=decision, kind="human")
                    rt.tracer.event(
                        trace_id=trace_id, node="approval", actor=approver, status=decision,
                        approval_id=st.approval.approval_id, guardrail_decision="HUMAN_" + decision,
                        output={"approver_role": role, "decision": decision},
                    )  # fmt: skip
                    if decision == "REJECT":
                        self._finalize(
                            st, Finish(Status.REJECTED, "A human reviewer rejected the proposed outreach; nothing was sent.")
                        )
                        await self._close(st, loaded)
                        return st
                    rt.approvals.grant(st.approval.approval_id, plan.idempotency_key)
                    st.status = Status.RUNNING
                    await self._drive(st, ident, Scratch(), "execute", expected_version=loaded)
                    return st

    def _authorize_decision(
        self, st: WorkflowState, approver: str, role: str, rationale: str, expected: int | None, sp: Any
    ) -> None:
        def deny(code: str, msg: str) -> None:
            sp.status = "DENIED"
            sp.set(error_code=code)
            raise PermissionError(msg)

        if st.status != Status.AWAITING_APPROVAL:
            deny("NOT_AWAITING_APPROVAL", "workflow is not awaiting approval")
        if role not in APPROVER_ROLES:
            deny("APPROVER_ROLE", "role not permitted to approve")
        if approver == st.requester:
            deny("SEGREGATION_OF_DUTIES", "requester cannot approve own request")
        if not rationale.strip():
            raise ValueError("rationale required")
        if expected is not None and st.version != expected:
            raise ConflictError("workflow modified concurrently")

    # ---- patient rights ---------------------------------------------------------
    def _rights_event(self, patient_ref: str, actor: str, node: str, **fields: Any) -> None:
        self.rt.tracer.event(trace_id="rights-" + patient_ref, node=node, actor=actor, agent=self.name, **fields)

    def withdraw_consent(self, patient_ref: str, actor: str) -> None:
        self.rt.backend.withdraw_consent(patient_ref)
        self._rights_event(patient_ref, actor, "consent_withdrawn")

    def delete_patient(self, patient_ref: str, actor: str) -> dict[str, Any]:
        rt = self.rt
        result = rt.memory.delete_patient(patient_ref)
        if result["blocked"] is None:
            result["workflows_purged"] = rt.store.purge_patient(patient_ref)
            tasks = [k for k, v in rt.backend.tasks.items() if v["patient_ref"] == patient_ref]
            for k in tasks:
                del rt.backend.tasks[k]
            result["review_tasks_purged"] = len(tasks)
        self._rights_event(
            patient_ref, actor, "deletion_request", output=result
        )  # audit trail itself is retained (immutable, pseudonymous)
        return result

    def correct_outcome(self, patient_ref: str, idx: int, delivery_status: str, actor: str) -> None:
        self.rt.memory.correct(patient_ref, idx, delivery_status)
        self._rights_event(patient_ref, actor, "correction_request", output={"idx": idx, "status": delivery_status})

    def access_export(self, patient_ref: str, actor: str) -> dict[str, Any]:
        rt = self.rt
        self._rights_event(patient_ref, actor, "access_request")
        return {
            "patient_ref": patient_ref,
            "intervention_history": [
                {"template_id": r["template_id"], "delivery_status": r["delivery_status"], "at": r["at"].isoformat()}
                for r in rt.memory.raw(patient_ref)
            ],
            "consent": (c.model_dump(mode="json") if (c := rt.backend.get_consent(patient_ref)) else None),
        }

    def apply_retention(self, actor: str) -> int:
        n = self.rt.memory.apply_retention()
        self._rights_event("all", actor, "retention_applied", output={"removed": n})
        return n

    def explain(self, trace_id: str) -> dict[str, Any]:
        """Patient-readable explanation for access requests."""
        st = self.rt.store.get(trace_id)
        if not st:
            raise KeyError(trace_id)
        r = st.risk_assessment
        return {
            "trace_id": trace_id,
            "what_happened": st.rationale or st.status.value,
            "status": st.status.value,
            "why": [f.detail for f in r.factors] if r else [],
            "limits": r.limitations if r else [],
            "human_review": bool(st.approval) or st.status in (Status.AWAITING_APPROVAL, Status.ESCALATED),
            "message_sent": bool(st.action_receipt),
        }
