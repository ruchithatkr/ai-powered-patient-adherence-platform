"""Skill: assess-adherence-risk. See skills/assess-adherence-risk/SKILL.md."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from domain.errors import SkillError
from domain.models import Factor, PatientContext, RiskAssessment, RiskBand
from workflows.policy import Policy

InferFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
SKILL_NAME = "assess-adherence-risk"


async def assess(ctx: PatientContext, infer: InferFn, policy: Policy, now: datetime) -> RiskAssessment:
    """`infer` is backed by the risk-model-infer MCP server (via the gateway)."""
    rp = policy.risk
    # 1. Validate schema, freshness, minimum data.
    if now - ctx.plan.verified_at > timedelta(days=policy.freshness.medication_plan_max_age_days):
        raise SkillError("STALE_MEDICATION_DATA", "Medication data is stale; no action taken. Refresh with the care team.")
    conf = [e for e in ctx.events if e.kind == "DOSE_CONFIRMED"]
    miss = [e for e in ctx.events if e.kind == "DOSE_MISSED_REPORTED"]
    late = [e for e in ctx.events if e.kind == "REFILL_LATE"]
    n = len(conf) + len(miss)
    # 2. Allowed features only (no demographics, no message-engagement signals).
    feats = {
        "n_obs": n,
        "adherence_ratio": round(len(conf) / n, 6) if n else 0.0,
        "late_refill_days": sum(e.days_late for e in late),
    }
    # 3. Approved model + confidence / missingness.
    out = await infer(feats)
    missingness = round(max(0.0, 1 - n / rp.expected_observations), 3)
    confidence = round(min(1.0, n / 10) * (1 - 0.5 * missingness), 3)
    score = float(out["score"])
    # 4. Plain-language, non-causal factor explanations.
    factors = [
        Factor(
            name="confirmed_doses",
            evidence_id="EV-events",
            detail=f"{len(conf)} of {n} recorded doses were confirmed in the observation window.",
        )
    ]
    if late:
        factors.append(
            Factor(
                name="late_refills",
                evidence_id="EV-refills",
                detail=f"{len(late)} refill(s) were recorded {feats['late_refill_days']} days late in total.",
            )
        )
    if missingness > 0.3:
        factors.append(
            Factor(
                name="limited_data",
                evidence_id="EV-events",
                detail=f"Only {n} of about {rp.expected_observations} expected observations are available.",
            )
        )
    # 5. Uncertainty / escalation thresholds (values come from workflows/policy.yaml, not code).
    if n < rp.min_observations or confidence < rp.min_confidence:
        band, step = RiskBand.ABSTAIN, "Route to human review: insufficient data for a reliable assessment."
    elif score < rp.band_cutoffs["medium"]:
        band, step = RiskBand.LOW, "No outreach needed; continue routine monitoring."
    elif score < rp.band_cutoffs["high"]:
        band, step = RiskBand.MEDIUM, "Consider a low-intrusion approved reminder or education message."
    else:
        band, step = RiskBand.HIGH, "Route to care team for human-led follow-up."
    return RiskAssessment(
        patient_ref=ctx.patient_ref,
        score=score,
        band=band,
        confidence=confidence,
        missingness=missingness,
        factors=factors,
        model=out["model"],
        model_version=out["model_version"],
        calibrated=False,
        limitations=[
            "Deterministic fallback model; calibration and subgroup performance NOT YET MEASURED.",
            f"Band cutoffs are {rp.status} placeholders pending model-risk approval.",
            "Reflects recorded events only; missed reports may not equal missed doses.",
            "Message delivery or opens are never used as adherence evidence.",
            "Factors describe patterns, not causes.",
        ],
        evidence_refs=["EV-events", "EV-refills", *[f"prov:{p}" for p in ctx.provenance]],
        recommended_next_step=step,
        evaluated_at=now,
    )
