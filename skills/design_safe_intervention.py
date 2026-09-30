"""Skill: design-safe-intervention. See skills/design-safe-intervention/SKILL.md."""
import hashlib
from datetime import datetime, timedelta, timezone

from domain.models import InterventionPlan, PatientContext, RiskAssessment, RiskBand
from domain.synthetic import FIRST_NAMES, TEMPLATES
from guardrails.checks import frequency_ok, next_allowed_time, in_quiet_hours

# Templates a clinician has pre-approved for standing use (recorded human decision).
STANDING_APPROVED = {"REMINDER_SIMPLE@1", "EDUCATION_ROUTINE@1", "EDUCATION_REFILL@1"}


class NoIntervention(Exception):
    """Raised with a user-safe reason when the least intrusive action is to do nothing."""


def design(ctx: PatientContext, risk: RiskAssessment, consented_channels: list[str], fetch_article,
           now: datetime | None = None) -> InterventionPlan:
    now = now or datetime.now(timezone.utc)
    if risk.band == RiskBand.LOW:
        raise NoIntervention("Low risk: no outreach is the least intrusive action.")
    if risk.band == RiskBand.ABSTAIN:
        raise NoIntervention("Low confidence: no automated outreach; routed to human review.")
    if not frequency_ok(ctx.prior_interventions, now):
        raise NoIntervention("Frequency cap reached (3 messages in 7 days); no further outreach.")
    prefs = ctx.prefs
    if prefs.language != "en":
        raise NoIntervention(f"No approved template in language '{prefs.language}'; routed to human review.")
    channel = prefs.preferred_channel if prefs.preferred_channel in consented_channels else None
    if channel is None:
        raise NoIntervention("No consented channel matches the patient's preference; routed to human review.")

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
    tpl = TEMPLATES[tid]

    # 2. Personalise ONLY within approved template fields.
    citation, kb_text = None, ""
    if tpl["kb"]:
        art = fetch_article(tpl["kb"])
        kb_text, citation = art["text"], f"{art['kb_id']} v{art['version']} - {art['source']}"
    body = tpl["body"].format(first_name=FIRST_NAMES.get(ctx.patient_ref, "there"),
                              medication_label=ctx.plan.medication_label.lower(),
                              time=ctx.plan.schedule_times_local[0], kb_text=kb_text)

    # 3. Timing (quiet hours in patient's verified timezone) & accessibility.
    send_at = None if not in_quiet_hours(prefs, now) else next_allowed_time(prefs, now)
    access = ["large_text_rendering"] if prefs.large_text else []

    # 4. Approval requirements.
    reasons = []
    if not ctx.prior_interventions:
        reasons.append("first_outreach")
    if risk.band == RiskBand.HIGH:
        reasons.append("high_risk_escalation")
    if f"{tid}@{tpl['version']}" not in STANDING_APPROVED:
        reasons.append("template_requires_review")
    day = now.strftime("%Y-%m-%d")
    key = hashlib.sha256(f"{ctx.patient_ref}|{tid}|{tpl['version']}|{day}".encode()).hexdigest()[:20]
    return InterventionPlan(
        template_id=tid, template_version=tpl["version"], channel=channel, body=body, citation=citation,
        requires_approval=bool(reasons), approval_reasons=reasons,
        rationale=f"{risk.band.value} risk; selected the least intrusive eligible template within frequency, "
                  f"consent and quiet-hour limits.",
        idempotency_key=key, expires_at=now + timedelta(hours=24),
        evidence_refs=risk.evidence_refs, routes_to_human=(tid == "SUPPORT_CALLBACK"),
        send_not_before=send_at, accessibility=access)
