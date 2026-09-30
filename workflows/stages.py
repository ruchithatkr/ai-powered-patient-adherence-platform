"""Stage handlers for the adherence workflow. Deterministic rules decide permissions, consent, emergencies and approval gates."""

from domain.errors import NoIntervention, WorkflowStop
from domain.models import GuardrailResult, Status, WorkflowState
from guardrails.checks import check_input, is_emergency
from mcp.gateway import CallIdentity, GatewayError
from observability.tracing import Span
from workflows.definition import (
    REQUESTER_ROLES,
    Continue,
    Finish,
    Outcome,
    Scratch,
    Stop,
    Wait,
    require,
)
from workflows.runtime import Runtime


def record_guardrails(rt: Runtime, st: WorkflowState, results: list[GuardrailResult]) -> None:
    st.guardrail_results += results
    for g in results:
        rt.metrics.inc("adherence_guardrail_decisions_total", guardrail=g.guardrail, decision=g.decision)
        rt.tracer.event(
            trace_id=st.trace_id, node=f"guardrail:{g.guardrail}", actor="guardrails", status=g.decision,
            guardrail_decision=g.decision, error_code=None if g.decision == "PASS" else g.guardrail.upper(),
        )  # fmt: skip


class IntakeStage:
    name = "intake"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        if st.requester_role not in REQUESTER_ROLES:
            return Stop("ROLE_NOT_AUTHORIZED")
        results = check_input(scratch.patient_message)
        record_guardrails(rt, st, results)
        if any(g.decision == "BLOCK" for g in results):
            return Stop(
                "INPUT_BLOCKED",
                rationale="Input was blocked by safety guardrails (possible injection, secret or direct identifier).",
            )
        if scratch.identity_patient_ref != st.patient_ref:
            return Stop("IDENTITY_MISMATCH")
        if is_emergency(scratch.patient_message):
            # Show approved safety guidance and route to trained staff. Never autonomous emergency escalation.
            st.review_task_id = await rt.reviewer.packet(
                ident, st.patient_ref, "emergency/adverse-event language", None, None, emergency=True
            )
            return Stop("SAFETY_ESCALATION", Status.ESCALATED, rt.policy.emergency_guidance)
        return Continue()


class AuthorizeStage:
    name = "authorize"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        res = await rt.consent_agent.run(ident, st.patient_ref)
        sp.set(output={"decision": res.decision, "policy": res.policy_id}, consent_policy=res.policy_id)
        st.consent_snapshot = res.consent
        if res.decision != "ALLOW":
            return Stop(f"CONSENT_{res.decision}", rationale=f"No action taken: {res.reason}.")
        return Continue()


class RetrieveStage:
    name = "retrieve"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        ctx = await rt.context_agent.run(ident, st.patient_ref, st.consent_snapshot)
        scratch.context = ctx
        st.medication_plan_version = ctx.plan.version
        st.evidence_refs = [f"prov:{p}" for p in ctx.provenance]
        sp.set(output={"plan_version": ctx.plan.version}, evidence_ids=st.evidence_refs)
        return Continue()


class AssessStage:
    name = "assess"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        risk = await rt.risk_agent.run(ident, require(scratch.context, "patient context"))
        st.risk_assessment = risk
        rt.metrics.inc("adherence_risk_band_total", band=risk.band.value)
        sp.set(
            skill="assess-adherence-risk", model=risk.model, band=risk.band.value, output={"band": risk.band.value},
            evidence_ids=risk.evidence_refs, guardrail_decision="PASS",
        )  # fmt: skip
        return Continue()


class PlanStage:
    name = "plan"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        ctx = require(scratch.context, "patient context")
        risk = require(st.risk_assessment, "risk assessment")
        consent = require(st.consent_snapshot, "consent snapshot")
        try:
            plan = await rt.planner.run(ident, ctx, risk, consent)
        except NoIntervention as e:
            sp.set(skill="design-safe-intervention", output={"no_intervention": str(e)})
            if e.needs_human:
                st.review_task_id = await rt.reviewer.packet(ident, st.patient_ref, str(e), risk, None)
                return Finish(Status.ESCALATED, str(e))
            return Finish(Status.COMPLETED, str(e))
        st.intervention_plan = plan
        sp.set(skill="design-safe-intervention", output={"template": plan.template_id}, evidence_ids=plan.evidence_refs)
        return Continue()


class SafetyCheckStage:
    name = "safety_check"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        results = await rt.safety.run(ident, require(st.intervention_plan, "intervention plan"))
        record_guardrails(rt, st, results)
        blocked = [g for g in results if g.decision == "BLOCK"]
        sp.set(guardrail_decision="BLOCK" if blocked else "PASS")
        if blocked:
            return Stop("CONTENT_BLOCKED", rationale="Planned message failed content-safety checks and was not sent.")
        return Continue()


class ApprovalStage:
    name = "approval"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        plan = require(st.intervention_plan, "intervention plan")
        risk = require(st.risk_assessment, "risk assessment")
        if plan.requires_approval or plan.routes_to_human:
            st.review_task_id = await rt.reviewer.packet(
                ident, st.patient_ref, "approval required: " + ",".join(plan.approval_reasons), risk, plan
            )
            return Wait()
        # Pre-approved template with a recorded standing clinician approval (a data record, see workflows/templates.yaml).
        tpl = rt.templates.get(plan.template_id)
        if tpl is None or not tpl.standing_approval.granted:
            raise WorkflowStop("TEMPLATE_NOT_STANDING_APPROVED", "Template has no standing approval; human approval is required.")
        st.approval = rt.reviewer.record(
            f"standing-approval:{tpl.standing_approval.record}", "standing-approval-record", "APPROVE",
            f"Standing approval for {plan.template_id}@{plan.template_version}",
        )  # fmt: skip
        rt.approvals.grant(st.approval.approval_id, plan.idempotency_key)
        rt.metrics.inc("adherence_approvals_total", decision="APPROVE", kind="standing")
        rt.tracer.event(
            trace_id=st.trace_id, node="approval", actor="standing-approval", status="APPROVE",
            approval_id=st.approval.approval_id, guardrail_decision="STANDING_APPROVE",
        )  # fmt: skip
        return Continue()


class ExecuteStage:
    name = "execute"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        plan = require(st.intervention_plan, "intervention plan")
        risk = require(st.risk_assessment, "risk assessment")
        # Defence in depth: no external write on low confidence, missing approval, or unresolved blocking alert.
        if risk.confidence < rt.policy.risk.min_confidence:
            return Stop("LOW_CONFIDENCE")
        if not st.approval or st.approval.decision != "APPROVE":
            return Stop("APPROVAL_MISSING")
        if rt.alerts.blocking():
            return Stop("UNRESOLVED_ALERT", rationale="An unresolved operational alert blocks external actions.")
        try:
            st.action_receipt = await rt.delivery.run(ident, st.patient_ref, plan, st.approval.approval_id)
        except GatewayError as e:
            # Non-idempotent write: never blindly retried; raise an operational alert.
            rt.tracer.event(
                trace_id=st.trace_id,
                node="alert",
                actor=rt.policy.workflow_version,
                status="ALERT",
                error_code=e.code,
                tool="notification-send",
            )
            raise WorkflowStop(e.code, "Delivery failed; not retried automatically. Operations alerted.") from e
        sp.set(output={"receipt": st.action_receipt.receipt_id}, action_receipt=st.action_receipt.receipt_id)
        return Continue()


class VerifyStage:
    name = "verify"

    async def run(self, rt: Runtime, st: WorkflowState, ident: CallIdentity, scratch: Scratch, sp: Span) -> Outcome:
        receipt = require(st.action_receipt, "action receipt")
        plan = require(st.intervention_plan, "intervention plan")
        rt.delivery.verify(receipt, plan)
        rt.memory.remember_outcome(st.patient_ref, plan.template_id, receipt.delivery_status)
        sp.set(action_receipt=receipt.receipt_id)
        return Finish(
            Status.COMPLETED,
            "Approved outreach was handed to the notification service (simulated in this prototype). "
            "A delivery receipt does not show that the message was read or that medicine was taken.",
        )


HANDLERS = {
    h.name: h
    for h in (
        IntakeStage(), AuthorizeStage(), RetrieveStage(), AssessStage(), PlanStage(),
        SafetyCheckStage(), ApprovalStage(), ExecuteStage(), VerifyStage(),
    )
}  # fmt: skip
