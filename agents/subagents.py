"""Sub-agents: narrow, schema-validated, deterministic in this prototype (no LLM is called).

Each agent run is a traced span recording agent/version, task, outcome, token usage (0 - no LLM) and a
PHI-safe handoff summary. The supervisor only combines outputs whose schemas validate.
"""

import asyncio
import uuid
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from domain.clock import Clock
from domain.errors import WorkflowStop
from domain.models import (
    ActionReceipt,
    Approval,
    Consent,
    GuardrailResult,
    InterventionPlan,
    PatientContext,
    RiskAssessment,
)
from guardrails.accessibility import check_accessibility
from guardrails.checks import check_content, consent_decision
from mcp.gateway import CallIdentity, Gateway
from memory.store import LongTermMemory
from observability.tracing import Span, Tracer
from skills import assess_adherence_risk as risk_skill
from skills import design_safe_intervention as intervention_skill
from workflows.policy import Policy
from workflows.templates import TemplateLibrary

VERSION = "1.1.0"


class BaseAgent:
    name = "agent"
    version = VERSION

    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer

    def span(self, ident: CallIdentity, task: str) -> AbstractAsyncContextManager[Span]:
        return self.tracer.span(
            trace_id=ident.trace_id, node=f"subagent:{self.name}", actor=self.name,
            agent=self.name, agent_version=self.version, task=task,
        )  # fmt: skip

    def skill_span(self, ident: CallIdentity, skill: str, **fields: Any) -> AbstractAsyncContextManager[Span]:
        return self.tracer.span(
            trace_id=ident.trace_id, node=f"skill:{skill}", actor=self.name, skill=skill, agent=self.name, **fields
        )


@dataclass
class ConsentResult:
    decision: str
    policy_id: str
    reason: str
    consent: Consent | None


class ConsentAccessAgent(BaseAgent):
    name = "consent-access-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, clock: Clock) -> None:
        super().__init__(tracer)
        self.gw, self.clock = gateway, clock

    async def run(self, ident: CallIdentity, patient_ref: str, channel: str | None = None) -> ConsentResult:
        async with self.span(ident, "verify consent scope, purpose, channel") as sp:
            raw = await self.gw.call("consent-registry-read", "get_consent", {"patient_ref": patient_ref}, ident=ident)
            consent = Consent(**raw) if raw else None
            decision, policy, reason = consent_decision(consent, ident.purpose, channel, self.clock.now())
            sp.set(guardrail_decision=decision, handoff_summary=f"{decision} ({policy})")
            return ConsentResult(decision, policy, reason, consent)


class PatientContextAgent(BaseAgent):
    name = "patient-context-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, memory: LongTermMemory, clock: Clock, policy: Policy) -> None:
        super().__init__(tracer)
        self.gw, self.memory, self.clock, self.policy = gateway, memory, clock, policy

    async def run(self, ident: CallIdentity, patient_ref: str, consent: Consent | None) -> PatientContext:
        async with self.span(ident, "retrieve medication plan, preferences, prior interventions") as sp:
            # Independent lookups run in parallel (records service + consented long-term memory).
            raw, remembered = await asyncio.gather(
                self.gw.call("patient-records-read", "get_context", {"patient_ref": patient_ref}, ident=ident),
                asyncio.to_thread(self.memory.recall, patient_ref, consent=consent, purpose=ident.purpose, now=self.clock.now()),
            )
            ctx = PatientContext(**raw)
            if not ctx.provenance:
                raise WorkflowStop("UNVERIFIED_RECORD", "Record provenance is missing; no action taken.")
            age = self.clock.now() - ctx.plan.verified_at
            if age > timedelta(days=self.policy.freshness.medication_plan_max_age_days):
                raise WorkflowStop(
                    "STALE_MEDICATION_DATA", "Medication data is stale; no action taken. Refresh with the care team."
                )
            ctx.prior_interventions = [*ctx.prior_interventions, *remembered]
            sp.set(
                evidence_ids=[f"prov:{p}" for p in ctx.provenance],
                handoff_summary=f"plan {ctx.plan.version}; {len(ctx.events)} events; {len(ctx.prior_interventions)} prior interventions",
            )
            return ctx


class AdherenceRiskAgent(BaseAgent):
    name = "adherence-risk-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, clock: Clock, policy: Policy) -> None:
        super().__init__(tracer)
        self.gw, self.clock, self.policy = gateway, clock, policy

    async def run(self, ident: CallIdentity, ctx: PatientContext) -> RiskAssessment:
        async with self.span(ident, "assess adherence risk") as sp:
            async with self.skill_span(ident, risk_skill.SKILL_NAME) as ssp:

                async def infer(features: dict[str, object]) -> dict[str, object]:
                    result: dict[str, object] = await self.gw.call(
                        "risk-model-infer", "infer", {"features": features}, ident=ident
                    )
                    return result

                risk = await risk_skill.assess(ctx, infer, self.policy, self.clock.now())
                ssp.set(model=risk.model, evidence_ids=risk.evidence_refs, band=risk.band.value)
            sp.set(handoff_summary=f"band={risk.band.value} confidence={risk.confidence}")
            return risk


class InterventionPlannerAgent(BaseAgent):
    name = "intervention-planner-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, clock: Clock, policy: Policy, templates: TemplateLibrary) -> None:
        super().__init__(tracer)
        self.gw, self.clock, self.policy, self.templates = gateway, clock, policy, templates

    async def run(self, ident: CallIdentity, ctx: PatientContext, risk: RiskAssessment, consent: Consent) -> InterventionPlan:
        async with self.span(ident, "design least-intrusive approved intervention") as sp:
            async with self.skill_span(ident, intervention_skill.SKILL_NAME) as ssp:

                async def article(kb_id: str) -> dict[str, object]:
                    result: dict[str, object] = await self.gw.call(
                        "knowledge-base-read", "get_article", {"kb_id": kb_id}, ident=ident
                    )
                    return result

                plan = await intervention_skill.design(
                    ctx, risk, consent.channels, article,
                    policy=self.policy, templates=self.templates, now=self.clock.now(),
                )  # fmt: skip
                ssp.set(evidence_ids=plan.evidence_refs, approval_reasons=plan.approval_reasons)
            sp.set(handoff_summary=f"template={plan.template_id}@{plan.template_version} channel={plan.channel}")
            return plan


class ContentSafetyAgent(BaseAgent):
    name = "content-safety-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, clock: Clock, policy: Policy, templates: TemplateLibrary) -> None:
        super().__init__(tracer)
        self.gw, self.clock, self.policy, self.templates = gateway, clock, policy, templates

    async def run(self, ident: CallIdentity, plan: InterventionPlan) -> list[GuardrailResult]:
        async with self.span(ident, "check message against approved library and verified source") as sp:
            res = [check_content(plan.body)]
            tpl = self.templates.get(plan.template_id)
            locale = tpl.approved_locale(plan.language) if tpl else None
            if not tpl or not locale or tpl.version != plan.template_version:
                res.append(
                    GuardrailResult(guardrail="template-allowlist", decision="BLOCK", detail="unapproved template/version/locale")
                )
            else:
                res.append(GuardrailResult(guardrail="template-allowlist", decision="PASS"))
                # The body must be exactly the approved template rendered with the allowed fields.
                try:
                    rendered = locale.body.format(**plan.template_fields)
                except (KeyError, IndexError):
                    rendered = None
                same = rendered == plan.body
                res.append(GuardrailResult(guardrail="template-integrity", decision="PASS" if same else "BLOCK",
                                           detail="" if same else "message differs from approved template"))  # fmt: skip
                if tpl.kb:
                    res.append(await self._verify_source(ident, plan, tpl.kb))
            if plan.expires_at < self.clock.now():
                res.append(GuardrailResult(guardrail="plan-expiry", decision="BLOCK", detail="plan expired"))
            ap = self.policy.accessibility
            res.append(check_accessibility(plan.body, ap.max_reading_grade, ap.max_sms_chars, plan.channel))
            blocked = [g.guardrail for g in res if g.decision == "BLOCK"]
            sp.set(guardrail_decision="BLOCK" if blocked else "PASS", handoff_summary=f"blocked={blocked}")
            return res

    async def _verify_source(self, ident: CallIdentity, plan: InterventionPlan, kb_id: str) -> GuardrailResult:
        art = await self.gw.call("knowledge-base-read", "get_article", {"kb_id": kb_id}, ident=ident)
        ok = (
            plan.template_fields.get("kb_text") == art["text"]
            and plan.citation is not None
            and plan.citation.startswith(f"{kb_id} v{art['version']}")
        )
        return GuardrailResult(
            guardrail="source-verification", decision="PASS" if ok else "BLOCK",
            detail="" if ok else "educational text does not match verified, cited source",
        )  # fmt: skip


class HumanReviewAgent(BaseAgent):
    name = "human-review-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, policy: Policy) -> None:
        super().__init__(tracer)
        self.gw, self.policy = gateway, policy

    async def packet(
        self, ident: CallIdentity, patient_ref: str, reason: str,
        risk: RiskAssessment | None, plan: InterventionPlan | None, emergency: bool = False,
    ) -> str:  # fmt: skip
        async with self.span(ident, "prepare evidence packet for human review") as sp:
            pkt = {
                "reason": reason,
                "risk": risk and {
                    "band": risk.band.value, "score": risk.score, "confidence": risk.confidence,
                    "factors": [f.detail for f in risk.factors], "limitations": risk.limitations,
                    "model": f"{risk.model}@{risk.model_version}",
                },
                "proposed_action": plan and {
                    "template": f"{plan.template_id}@{plan.template_version}", "channel": plan.channel,
                    "approval_reasons": plan.approval_reasons, "citation": plan.citation,
                },
                "safety_guidance": self.policy.emergency_guidance if emergency else None,
            }  # fmt: skip
            task = await self.gw.call("care-team-review", "create_task", {"patient_ref": patient_ref, "packet": pkt}, ident=ident)
            sp.set(handoff_summary=f"review task {task['task_id']} created")
            return str(task["task_id"])

    @staticmethod
    def record(
        approver: str, role: str, decision: Literal["APPROVE", "REJECT"], rationale: str, conditions: str = ""
    ) -> Approval:
        return Approval(
            approval_id="APR-" + uuid.uuid4().hex[:8], approver=approver, role=role,
            decision=decision, rationale=rationale, conditions=conditions,
        )  # fmt: skip


class DeliveryAgent(BaseAgent):
    name = "delivery-agent"

    def __init__(self, tracer: Tracer, gateway: Gateway, clock: Clock) -> None:
        super().__init__(tracer)
        self.gw, self.clock = gateway, clock

    async def run(self, ident: CallIdentity, patient_ref: str, plan: InterventionPlan, approval_id: str) -> ActionReceipt:
        async with self.span(ident, "execute approved idempotent send") as sp:
            r = await self.gw.call(
                "notification-send", "send",
                {
                    "idempotency_key": plan.idempotency_key, "channel": plan.channel, "template_id": plan.template_id,
                    "body": plan.body, "patient_ref": patient_ref,
                },
                ident=ident, approval_token=approval_id,
            )  # fmt: skip
            scheduled = plan.send_not_before is not None and plan.send_not_before > self.clock.now()
            status = (
                f"SCHEDULED_AFTER_QUIET_HOURS:{plan.send_not_before.isoformat(timespec='minutes')}"
                if scheduled and plan.send_not_before
                else r["delivery_status"]
            )
            receipt = ActionReceipt(
                receipt_id=r["receipt_id"], tool="notification-send", idempotency_key=plan.idempotency_key,
                delivery_status=status, simulated=r["simulated"],
            )  # fmt: skip
            sp.set(action_receipt=receipt.receipt_id, handoff_summary="handed to notification service")
            return receipt

    def verify(self, receipt: ActionReceipt, plan: InterventionPlan) -> None:
        """Verify the delivery receipt only; never infer engagement or medication intake from it."""
        if receipt.idempotency_key != plan.idempotency_key or not receipt.receipt_id:
            raise WorkflowStop("RECEIPT_MISMATCH", "Delivery receipt did not match the approved action; flagged for operations.")
