"""Sub-agents: narrow, schema-validated, deterministic in this prototype (no LLM is called).
Each returns typed output; the supervisor only combines outputs whose schemas validate."""
import uuid
from datetime import datetime, timedelta, timezone

from domain.models import (ActionReceipt, Approval, Consent, GuardrailResult, InterventionPlan, PatientContext,
                           RiskAssessment)
from domain.synthetic import EMERGENCY_GUIDANCE, TEMPLATES
from guardrails.checks import check_content, consent_decision
from mcp.gateway import Gateway
from skills import assess_adherence_risk as risk_skill
from skills import design_safe_intervention as intervention_skill

VERSION = "1.0.0"


class Ctx:
    """Call identity passed to every tool call."""
    def __init__(self, gateway: Gateway, actor: str, role: str, purpose: str, trace_id: str, parent_id=None):
        self.gw, self.actor, self.role, self.purpose, self.trace_id, self.parent_id = gateway, actor, role, purpose, trace_id, parent_id

    def call(self, server, op, params, **kw):
        return self.gw.call(server, op, params, actor=self.actor, role=self.role, purpose=self.purpose,
                            trace_id=self.trace_id, parent_id=self.parent_id, **kw)


class ConsentAccessAgent:
    name = "consent-access-agent"

    def run(self, c: Ctx, patient_ref: str, channel: str | None = None):
        raw = c.call("consent-registry-read", "get_consent", {"patient_ref": patient_ref})
        consent = Consent(**raw) if raw else None
        decision, policy, reason = consent_decision(consent, c.purpose, channel, datetime.now(timezone.utc))
        return decision, policy, reason, consent


class PatientContextAgent:
    name = "patient-context-agent"

    def run(self, c: Ctx, patient_ref: str) -> PatientContext:
        return PatientContext(**c.call("patient-records-read", "get_context", {"patient_ref": patient_ref}))


class AdherenceRiskAgent:
    name = "adherence-risk-agent"

    def run(self, c: Ctx, ctx: PatientContext) -> RiskAssessment:
        return risk_skill.assess(ctx, lambda f: c.call("risk-model-infer", "infer", {"features": f}))


class InterventionPlannerAgent:
    name = "intervention-planner-agent"

    def run(self, c: Ctx, ctx: PatientContext, risk: RiskAssessment, consent: Consent) -> InterventionPlan:
        return intervention_skill.design(
            ctx, risk, consent.channels, lambda kb: c.call("knowledge-base-read", "get_article", {"kb_id": kb}))


class ContentSafetyAgent:
    name = "content-safety-agent"

    def run(self, plan: InterventionPlan) -> list[GuardrailResult]:
        res = [check_content(plan.body)]
        tpl = TEMPLATES.get(plan.template_id)
        if not tpl or tpl["version"] != plan.template_version:
            res.append(GuardrailResult(guardrail="template-allowlist", decision="BLOCK", detail="unapproved template/version"))
        else:
            res.append(GuardrailResult(guardrail="template-allowlist", decision="PASS"))
        if tpl and tpl["kb"] and not plan.citation:
            res.append(GuardrailResult(guardrail="citation-required", decision="BLOCK", detail="educational content lacks citation"))
        if plan.expires_at < datetime.now(timezone.utc):
            res.append(GuardrailResult(guardrail="plan-expiry", decision="BLOCK", detail="plan expired"))
        return res


class HumanReviewAgent:
    name = "human-review-agent"

    def packet(self, c: Ctx, patient_ref: str, reason: str, risk: RiskAssessment | None, plan: InterventionPlan | None) -> str:
        pkt = {"reason": reason,
               "risk": risk and {"band": risk.band.value, "score": risk.score, "confidence": risk.confidence,
                                 "factors": [f.detail for f in risk.factors], "limitations": risk.limitations,
                                 "model": f"{risk.model}@{risk.model_version}"},
               "proposed_action": plan and {"template": f"{plan.template_id}@{plan.template_version}", "channel": plan.channel,
                                            "approval_reasons": plan.approval_reasons, "citation": plan.citation},
               "safety_guidance": EMERGENCY_GUIDANCE if "emergency" in reason else None}
        return c.call("care-team-review", "create_task", {"patient_ref": patient_ref, "packet": pkt})["task_id"]

    @staticmethod
    def record(approver: str, role: str, decision: str, rationale: str, conditions: str = "") -> Approval:
        return Approval(approval_id="APR-" + uuid.uuid4().hex[:8], approver=approver, role=role, decision=decision,
                        rationale=rationale, conditions=conditions)


class DeliveryAgent:
    name = "delivery-agent"

    def run(self, c: Ctx, patient_ref: str, plan: InterventionPlan, approval_id: str) -> ActionReceipt:
        if plan.send_not_before and plan.send_not_before > datetime.now(timezone.utc):
            status = f"SCHEDULED_AFTER_QUIET_HOURS:{plan.send_not_before.isoformat(timespec='minutes')}"
        else:
            status = None
        r = c.call("notification-send", "send",
                   {"idempotency_key": plan.idempotency_key, "channel": plan.channel, "template_id": plan.template_id,
                    "body": plan.body, "patient_ref": patient_ref}, approval_token=approval_id)
        # Verify delivery receipt only; never infer engagement or medication intake.
        return ActionReceipt(receipt_id=r["receipt_id"], tool="notification-send", idempotency_key=plan.idempotency_key,
                             delivery_status=status or r["delivery_status"], simulated=r["simulated"])
