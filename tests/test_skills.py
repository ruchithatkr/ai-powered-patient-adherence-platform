from datetime import datetime, timedelta, timezone
import pytest
from domain.synthetic import build
from skills.assess_adherence_risk import SkillError, assess
from skills.design_safe_intervention import NoIntervention, design
from mcp.gateway import Backend

B = Backend()
infer = B.infer


def ctx(ref):
    return build()[ref]["context"]


@pytest.mark.parametrize("ref,band", [("P-001", "LOW"), ("P-002", "MEDIUM"), ("P-003", "HIGH"), ("P-004", "ABSTAIN")])
def test_bands(ref, band):
    r = assess(ctx(ref), infer)
    assert r.band.value == band and not r.calibrated and r.limitations and r.evidence_refs


def test_stale_data_raises():
    with pytest.raises(SkillError):
        assess(ctx("P-006"), infer)


def test_explanations_make_no_causal_claims():
    for ref in ("P-002", "P-003"):
        for f in assess(ctx(ref), infer).factors:
            assert "because" not in f.detail.lower() and "caused" not in f.detail.lower()


def test_high_risk_plan_requires_approval_and_routes_human():
    c = ctx("P-003")
    p = design(c, assess(c, infer), ["sms", "email"], B.article)
    assert p.template_id == "SUPPORT_CALLBACK" and p.requires_approval and p.routes_to_human


def test_education_has_citation_and_idempotency_key_is_stable():
    c = ctx("P-007")
    c.prior_interventions = []
    r = assess(c, infer)
    a, b = design(c, r, ["sms"], B.article), design(c, r, ["sms"], B.article)
    assert a.citation and a.idempotency_key == b.idempotency_key


def test_unconsented_channel_and_low_risk_produce_no_plan():
    c = ctx("P-003")
    with pytest.raises(NoIntervention):
        design(c, assess(c, infer), ["sms"], B.article)   # patient prefers email
    c1 = ctx("P-001")
    with pytest.raises(NoIntervention):
        design(c1, assess(c1, infer), ["sms"], B.article)
