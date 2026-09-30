"""Skill: design-safe-intervention. See skills/design-safe-intervention/SKILL.md."""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from domain.errors import NoIntervention, WorkflowStop
from domain.models import InterventionPlan, PatientContext, RiskAssessment, RiskBand
from guardrails.checks import frequency_ok, in_quiet_hours, next_allowed_time
from workflows.policy import Policy
from workflows.templates import TemplateLibrary

ArticleFn = Callable[[str], Awaitable[dict[str, Any]]]
SKILL_NAME = "design-safe-intervention"


async def design(
    ctx: PatientContext,
    risk: RiskAssessment,
    consented_channels: list[str],
    fetch_article: ArticleFn,
    *,
    policy: Policy,
    templates: TemplateLibrary,
    now: datetime,
) -> InterventionPlan:
    if risk.band == RiskBand.LOW:
        raise NoIntervention("Low risk: no outreach is the least intrusive action.")
    if risk.band == RiskBand.ABSTAIN:
        raise NoIntervention("Low confidence: no automated outreach; routed to human review.", needs_human=True)
    ip = policy.intervention
    if any(p.outcome in ip.blocking_outcomes for p in ctx.prior_interventions):
        raise NoIntervention("Patient previously opted out or complained; routed to human review.", needs_human=True)
    cap = ip.frequency_cap
    if not frequency_ok(ctx.prior_interventions, now, cap.max_messages, cap.window_days):
        raise NoIntervention(
            f"Frequency cap reached ({cap.max_messages} messages in {cap.window_days} days); no further outreach."
        )
    prefs = ctx.prefs
    channel = prefs.preferred_channel if prefs.preferred_channel in consented_channels else None
    if channel is None:
        raise NoIntervention("No consented channel matches the patient's preference; routed to human review.", needs_human=True)

    # 1. Least intrusive eligible action.
    late = any(e.kind == "REFILL_LATE" for e in ctx.events)
    had_education = any(p.template_id.startswith("EDUCATION") for p in ctx.prior_interventions)
    if risk.band == RiskBand.HIGH:
        tid = "SUPPORT_CALLBACK"
    elif late:
        tid = "EDUCATION_REFILL"
    elif not had_education:
        tid = "EDUCATION_ROUTINE"
    else:
        tid = "REMINDER_SIMPLE"
    tpl = templates.get(tid)
    if tpl is None:
        raise WorkflowStop("TEMPLATE_MISSING", f"Template {tid} is not in the approved library; no action taken.")
    locale = tpl.approved_locale(prefs.language)
    if locale is None:
        raise NoIntervention(f"No approved template in language '{prefs.language}'; routed to human review.", needs_human=True)

    # 2. Personalise ONLY within approved template fields.
    citation, kb_text = None, ""
    if tpl.kb:
        art = await fetch_article(tpl.kb)
        kb_text, citation = art["text"], f"{art['kb_id']} v{art['version']} - {art['source']}"
    fields = {
        "first_name": TemplateLibrary.safe_first_name(ctx.first_name),
        "medication_label": ctx.plan.medication_label.lower(),
        "time": ctx.plan.schedule_times_local[0],
        "kb_text": kb_text,
    }
    body = locale.body.format(**fields)

    # 3. Timing (quiet hours in the patient's verified timezone) and accessibility hints.
    send_at = next_allowed_time(prefs, now) if in_quiet_hours(prefs, now) else None
    access = ["large_text_rendering"] if prefs.large_text else []

    # 4. Approval requirements.
    reasons = []
    if not ctx.prior_interventions:
        reasons.append("first_outreach")
    if risk.band == RiskBand.HIGH:
        reasons.append("high_risk_escalation")
    if not tpl.standing_approval.granted:
        reasons.append("template_requires_review")
    day = now.strftime("%Y-%m-%d")
    key = hashlib.sha256(f"{ctx.patient_ref}|{tid}|{tpl.version}|{day}".encode()).hexdigest()[:20]
    return InterventionPlan(
        template_id=tid,
        template_version=tpl.version,
        channel=channel,
        body=body,
        citation=citation,
        requires_approval=bool(reasons),
        approval_reasons=reasons,
        rationale=f"{risk.band.value} risk; selected the least intrusive eligible template within frequency, consent and quiet-hour limits.",
        idempotency_key=key,
        expires_at=now + timedelta(hours=ip.plan_expiry_hours),
        evidence_refs=risk.evidence_refs,
        routes_to_human=tpl.high_touch,
        send_not_before=send_at,
        accessibility=access,
        language=prefs.language,
        template_fields=fields,
    )
