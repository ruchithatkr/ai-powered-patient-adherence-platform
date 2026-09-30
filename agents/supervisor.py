"""adherence-supervisor: owns workflow state; deterministic routing for permissions, consent, emergency, approval gates."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.subagents import (AdherenceRiskAgent, ConsentAccessAgent, ContentSafetyAgent, Ctx, DeliveryAgent,
                              HumanReviewAgent, InterventionPlannerAgent, PatientContextAgent)
from domain.models import GuardrailResult, Status, WorkflowState
from domain.synthetic import EMERGENCY_GUIDANCE
from guardrails.checks import check_input, is_emergency
from mcp.gateway import Backend, Gateway, GatewayError
from memory.store import LongTermMemory, WorkflowStore
from observability.audit import AuditStore
from skills.assess_adherence_risk import SkillError
from skills.design_safe_intervention import NoIntervention
from workflows.definition import (APPROVER_ROLES, POLICY_VERSION, REQUESTER_ROLES, STALE_DAYS, WORKFLOW_VERSION)


class Supervisor:
    name = "adherence-supervisor"

    def __init__(self, audit: AuditStore | None = None, backend: Backend | None = None,
                 store: WorkflowStore | None = None, memory: LongTermMemory | None = None):
        self.audit = audit or AuditStore()
        self.backend = backend or Backend()
        self.gw = Gateway(self.audit, self.backend)
        self.store = store or WorkflowStore()
        self.memory = memory or LongTermMemory()
        self.consent_agent, self.context_agent = ConsentAccessAgent(), PatientContextAgent()
        self.risk_agent, self.planner = AdherenceRiskAgent(), InterventionPlannerAgent()
        self.safety, self.reviewer, self.delivery = ContentSafetyAgent(), HumanReviewAgent(), DeliveryAgent()

    # ---- helpers -------------------------------------------------------------
    def _ev(self, st, node, status="OK", **kw):
        return self.audit.append(trace_id=st.trace_id, actor=self.name, node=node, status=status,
                                 request_id=st.request_id, workflow_version=WORKFLOW_VERSION,
                                 policy_version=POLICY_VERSION, agent=self.name, **kw)

    def _stop(self, st: WorkflowState, reason: str, status=Status.STOPPED, rationale: str = ""):
        st.status, st.stop_reason, st.rationale = status, reason, rationale or f"Workflow stopped: {reason}."
        self._ev(st, "stop", status="STOPPED", error_code=reason)
        return self._finish(st)

    def _finish(self, st: WorkflowState):
        st.stage = "close"
        self._ev(st, "final", status=st.status.value, output={"status": st.status.value, "stop": st.stop_reason})
        return self.store.put(st)

    def _ctx(self, st, role=None, actor=None):
        return Ctx(self.gw, actor or st.requester, role or st.requester_role, st.purpose, st.trace_id)

    # ---- entry point ----------------------------------------------------------
    def start(self, *, patient_ref: str, purpose: str, requester: str, requester_role: str,
              identity_patient_ref: str, patient_message: str = "") -> WorkflowState:
        st = WorkflowState(trace_id="tr-" + uuid.uuid4().hex[:12], request_id="rq-" + uuid.uuid4().hex[:8],
                           patient_ref=patient_ref, purpose=purpose, requester=requester, requester_role=requester_role)
        self._ev(st, "request", input={"patient_ref": patient_ref, "purpose": purpose, "role": requester_role})
        self._ev(st, "agent_plan", output=["consent-access", "patient-context", "adherence-risk", "intervention-planner",
                                           "content-safety", "human-review", "delivery"])
        c = self._ctx(st)

        # -- intake: input guardrails (deterministic) --
        if requester_role not in REQUESTER_ROLES:
            return self._stop(st, "ROLE_NOT_AUTHORIZED")
        gr = check_input(patient_message)
        st.guardrail_results += gr
        if any(g.decision == "BLOCK" for g in gr):
            return self._stop(st, "INPUT_BLOCKED", rationale="Input was blocked by safety guardrails (possible injection, secret or direct identifier).")
        if identity_patient_ref != patient_ref:
            return self._stop(st, "IDENTITY_MISMATCH")
        if is_emergency(patient_message):
            # Show approved safety guidance and route immediately to trained staff. No autonomous emergency escalation.
            try:
                st.review_task_id = self.reviewer.packet(c, patient_ref, "emergency/adverse-event language", None, None)
            except GatewayError as e:
                return self._stop(st, e.code)
            return self._stop(st, "SAFETY_ESCALATION", status=Status.ESCALATED, rationale=EMERGENCY_GUIDANCE)

        # -- authorize --
        st.stage = "authorize"
        try:
            decision, policy, reason, consent = self.consent_agent.run(c, patient_ref)
        except GatewayError as e:
            return self._stop(st, e.code)
        self._ev(st, "stage:authorize", output={"decision": decision, "policy": policy}, consent_policy=policy)
        st.consent_snapshot = consent
        if decision != "ALLOW":
            return self._stop(st, f"CONSENT_{decision}", rationale=f"No action taken: {reason}.")

        # -- retrieve (independent lookups in parallel) --
        st.stage = "retrieve"
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_ctx = ex.submit(self.context_agent.run, c, patient_ref)
                f_con = ex.submit(self.consent_agent.run, c, patient_ref, None)
                ctx, _ = f_ctx.result(), f_con.result()
        except GatewayError as e:
            return self._stop(st, e.code)
        if datetime.now(timezone.utc) - ctx.plan.verified_at > timedelta(days=STALE_DAYS):
            return self._stop(st, "STALE_MEDICATION_DATA", rationale="Medication data is stale; no action taken. Refresh with the care team.")
        st.medication_plan_version = ctx.plan.version
        st.evidence_refs = [f"prov:{p}" for p in ctx.provenance]
        self._ev(st, "stage:retrieve", output={"plan_version": ctx.plan.version}, evidence_ids=st.evidence_refs)

        # -- assess --
        st.stage = "assess"
        try:
            risk = self.risk_agent.run(c, ctx)
        except SkillError as e:
            return self._stop(st, str(e))
        except GatewayError as e:
            return self._stop(st, e.code)
        st.risk_assessment = risk
        self._ev(st, "stage:assess", skill="assess-adherence-risk", model=risk.model, model_version=risk.model_version,
                 output={"band": risk.band.value}, evidence_ids=risk.evidence_refs, guardrail_decision="PASS")

        # -- plan --
        st.stage = "plan"
        try:
            plan = self.planner.run(c, ctx, risk, consent)
        except NoIntervention as e:
            st.rationale = str(e)
            needs_human = risk.band.value == "ABSTAIN" or "human review" in str(e)
            if needs_human:
                try:
                    st.review_task_id = self.reviewer.packet(c, patient_ref, str(e), risk, None)
                except GatewayError as ge:
                    return self._stop(st, ge.code)
                st.status = Status.ESCALATED
            else:
                st.status = Status.COMPLETED
            self._ev(st, "stage:plan", skill="design-safe-intervention", output={"no_intervention": str(e)})
            return self._finish(st)
        except GatewayError as e:
            return self._stop(st, e.code)
        st.intervention_plan = plan
        self._ev(st, "stage:plan", skill="design-safe-intervention", output={"template": plan.template_id},
                 evidence_ids=plan.evidence_refs)

        # -- safety check (fail closed) --
        st.stage = "safety_check"
        results = self.safety.run(plan)
        st.guardrail_results += results
        blocked = [g for g in results if g.decision == "BLOCK"]
        self._ev(st, "stage:safety_check", status="BLOCK" if blocked else "OK", guardrail_decision="BLOCK" if blocked else "PASS")
        if blocked:
            return self._stop(st, "CONTENT_BLOCKED", rationale="Planned message failed content-safety checks and was not sent.")

        # -- approval gate --
        st.stage = "approval"
        if plan.requires_approval or plan.routes_to_human:
            try:
                st.review_task_id = self.reviewer.packet(c, patient_ref, "approval required: " + ",".join(plan.approval_reasons), risk, plan)
            except GatewayError as e:
                return self._stop(st, e.code)
            st.status = Status.AWAITING_APPROVAL
            self._ev(st, "approval_requested", status="PENDING", approval_id=st.review_task_id)
            return self.store.put(st)
        # Pre-approved template with a recorded standing clinician approval.
        st.approval = self.reviewer.record("clinician.standing-approval", "clinician", "APPROVE",
                                           f"Standing approval for {plan.template_id}@{plan.template_version}")
        self.gw.valid_approvals.add(st.approval.approval_id)
        self._ev(st, "approval", approval_id=st.approval.approval_id, output={"standing": True})
        return self._execute(st, c)

    # ---- human decision -------------------------------------------------------
    def decide(self, trace_id: str, *, approver: str, role: str, decision: str, rationale: str, conditions: str = "",
               expected_version: Optional[int] = None) -> WorkflowState:
        st = self.store.get(trace_id)
        if not st:
            raise KeyError("workflow not found or expired")
        if st.status != Status.AWAITING_APPROVAL:
            raise PermissionError("workflow is not awaiting approval")
        if role not in APPROVER_ROLES:
            self._ev(st, "approval", status="DENIED", error_code="APPROVER_ROLE")
            raise PermissionError("role not permitted to approve")
        if approver == st.requester:
            self._ev(st, "approval", status="DENIED", error_code="SEGREGATION_OF_DUTIES")
            raise PermissionError("requester cannot approve own request")
        if not rationale.strip():
            raise ValueError("rationale required")
        if expected_version is not None and st.version != expected_version:
            raise PermissionError("stale version")
        if st.intervention_plan.expires_at < datetime.now(timezone.utc):
            return self._stop(st, "PLAN_EXPIRED")
        # Re-check consent at execution time (withdrawal may have occurred while waiting).
        c = self._ctx(st)
        d, _, reason, _ = self.consent_agent.run(c, st.patient_ref, st.intervention_plan.channel)
        if d != "ALLOW":
            return self._stop(st, f"CONSENT_{d}", rationale=f"Consent changed while awaiting approval: {reason}. No message sent.")
        st.approval = self.reviewer.record(approver, role, decision, rationale, conditions)
        self._ev(st, "approval", status=decision, approval_id=st.approval.approval_id,
                 output={"approver_role": role, "decision": decision}, guardrail_decision="HUMAN_" + decision)
        if decision == "REJECT":
            st.status, st.rationale = Status.REJECTED, "A human reviewer rejected the proposed outreach; nothing was sent."
            return self._finish(st)
        self.gw.valid_approvals.add(st.approval.approval_id)
        return self._execute(st, c)

    # ---- execute / verify / close ---------------------------------------------
    def _execute(self, st: WorkflowState, c: Ctx):
        st.stage = "execute"
        plan = st.intervention_plan
        try:
            st.action_receipt = self.delivery.run(c, st.patient_ref, plan, st.approval.approval_id)
        except GatewayError as e:
            # Non-idempotent write: never blindly retried; raise an operational alert.
            self.audit.append(trace_id=st.trace_id, actor=self.name, node="alert", status="ALERT", error_code=e.code,
                              tool="notification-send")
            return self._stop(st, e.code, rationale="Delivery failed; not retried automatically. Operations alerted.")
        st.stage = "verify"
        self._ev(st, "stage:execute", output={"receipt": st.action_receipt.receipt_id}, action_receipt=st.action_receipt.receipt_id)
        self.memory.remember_outcome(st.patient_ref, plan.template_id, st.action_receipt.delivery_status)
        st.status = Status.COMPLETED
        st.rationale = ("Approved outreach was handed to the notification service (simulated in this prototype). "
                        "A delivery receipt does not show that the message was read or that medicine was taken.")
        return self._finish(st)

    # ---- patient rights -------------------------------------------------------
    def withdraw_consent(self, patient_ref: str, actor: str):
        self.backend.withdraw_consent(patient_ref)
        self.audit.append(trace_id="rights-" + patient_ref, actor=actor, node="consent_withdrawn", agent=self.name)

    def delete_patient(self, patient_ref: str, actor: str) -> dict:
        r = self.memory.delete_patient(patient_ref)
        r["workflows_purged"] = self.store.purge_patient(patient_ref)
        self.audit.append(trace_id="rights-" + patient_ref, actor=actor, node="deletion_request", agent=self.name,
                          output=r)   # audit trail itself is retained (immutable, pseudonymous)
        return r

    def explain(self, trace_id: str) -> dict:
        """Patient-readable explanation for access requests."""
        st = self.store.get(trace_id)
        if not st:
            raise KeyError(trace_id)
        r = st.risk_assessment
        return {"trace_id": trace_id, "what_happened": st.rationale or st.status.value, "status": st.status.value,
                "why": [f.detail for f in r.factors] if r else [],
                "limits": r.limitations if r else [],
                "human_review": bool(st.approval) or st.status in (Status.AWAITING_APPROVAL, Status.ESCALATED),
                "message_sent": bool(st.action_receipt)}
