"""Skill: assess-adherence-risk. See skills/assess-adherence-risk/SKILL.md."""
from datetime import datetime, timedelta, timezone

from domain.models import Factor, PatientContext, RiskAssessment, RiskBand

STALE_DAYS = 30
MIN_OBS = 5
EXPECTED_OBS = 14
CONF_MIN = 0.6
BANDS = (0.25, 0.5)   # thresholds are placeholders; production values belong in evals/thresholds.yaml


class SkillError(Exception):
    pass


def assess(ctx: PatientContext, infer, now: datetime | None = None) -> RiskAssessment:
    """`infer` is a callable(features)->dict backed by the risk-model-infer MCP server."""
    now = now or datetime.now(timezone.utc)
    # 1. Validate schema, freshness, minimum data.
    if now - ctx.plan.verified_at > timedelta(days=STALE_DAYS):
        raise SkillError("STALE_MEDICATION_DATA")
    conf = [e for e in ctx.events if e.kind == "DOSE_CONFIRMED"]
    miss = [e for e in ctx.events if e.kind == "DOSE_MISSED_REPORTED"]
    late = [e for e in ctx.events if e.kind == "REFILL_LATE"]
    n = len(conf) + len(miss)
    # 2. Allowed features only (no demographics, no message-engagement signals).
    feats = {"n_obs": n, "adherence_ratio": (len(conf) / n) if n else 0.0,
             "late_refill_days": sum(e.days_late for e in late)}
    # 3. Approved model + confidence / missingness.
    out = infer(feats)
    missingness = round(max(0.0, 1 - n / EXPECTED_OBS), 3)
    confidence = round(min(1.0, n / 10) * (1 - 0.5 * missingness), 3)
    score = out["score"]
    # 4. Plain-language, non-causal factor explanations.
    factors = [Factor(name="confirmed_doses", evidence_id="EV-events",
                      detail=f"{len(conf)} of {n} recorded doses were confirmed in the observation window.")]
    if late:
        factors.append(Factor(name="late_refills", evidence_id="EV-refills",
                              detail=f"{len(late)} refill(s) were recorded {feats['late_refill_days']} days late in total."))
    if missingness > 0.3:
        factors.append(Factor(name="limited_data", evidence_id="EV-events",
                              detail=f"Only {n} of about {EXPECTED_OBS} expected observations are available."))
    # 5. Uncertainty / escalation thresholds.
    if n < MIN_OBS or confidence < CONF_MIN:
        band, step = RiskBand.ABSTAIN, "Route to human review: insufficient data for a reliable assessment."
    elif score < BANDS[0]:
        band, step = RiskBand.LOW, "No outreach needed; continue routine monitoring."
    elif score < BANDS[1]:
        band, step = RiskBand.MEDIUM, "Consider a low-intrusion approved reminder or education message."
    else:
        band, step = RiskBand.HIGH, "Route to care team for human-led follow-up."
    return RiskAssessment(
        patient_ref=ctx.patient_ref, score=score, band=band, confidence=confidence, missingness=missingness,
        factors=factors, model=out["model"], model_version=out["model_version"], calibrated=False,
        limitations=["Deterministic fallback model; calibration and subgroup performance NOT YET MEASURED.",
                     "Reflects recorded events only; missed reports may not equal missed doses.",
                     "Message delivery or opens are never used as adherence evidence.",
                     "Factors describe patterns, not causes."],
        evidence_refs=["EV-events", "EV-refills"] + [f"prov:{p}" for p in ctx.provenance],
        recommended_next_step=step, evaluated_at=now)
