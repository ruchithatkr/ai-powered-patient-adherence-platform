import dataclasses
from datetime import timedelta

import pytest

from domain.errors import NoIntervention, SkillError
from domain.models import PriorIntervention
from domain.synthetic import build
from mcp.adapters import RiskModelAdapter
from skills.assess_adherence_risk import assess
from skills.design_safe_intervention import design
from tests.conftest import T0


@pytest.fixture
def infer(rt):
    adapter = RiskModelAdapter(rt.backend)

    async def _infer(features):
        return await adapter.invoke("infer", {"features": features})

    return _infer


@pytest.fixture
def article(rt):
    async def _a(kb):
        return {"kb_id": kb, **rt.backend.kb[kb]}

    return _a


def ctx(ref):
    return build(T0)[ref]["context"]


@pytest.mark.parametrize("ref,band", [("P-001", "LOW"), ("P-002", "MEDIUM"), ("P-003", "HIGH"), ("P-004", "ABSTAIN")])
async def test_bands(rt, infer, ref, band):
    r = await assess(ctx(ref), infer, rt.policy, T0)
    assert r.band.value == band and not r.calibrated and r.limitations and r.evidence_refs


async def test_stale_data_raises(rt, infer):
    with pytest.raises(SkillError):
        await assess(ctx("P-006"), infer, rt.policy, T0)


async def test_missing_data_lowers_confidence(rt, infer):
    r = await assess(ctx("P-004"), infer, rt.policy, T0)
    assert r.missingness > 0.5 and r.confidence < rt.policy.risk.min_confidence
    assert any(f.name == "limited_data" for f in r.factors)


async def test_explanations_make_no_causal_claims(rt, infer):
    for ref in ("P-002", "P-003"):
        for f in (await assess(ctx(ref), infer, rt.policy, T0)).factors:
            assert not any(w in f.detail.lower() for w in ("because", "caused", "due to", "reason"))


async def test_message_events_are_never_adherence_evidence(rt, infer):
    seen = {}

    async def spy(features):
        seen.update(features)
        return await infer(features)

    await assess(ctx("P-003"), spy, rt.policy, T0)
    assert set(seen) == {"n_obs", "adherence_ratio", "late_refill_days"}  # only allowed features reach the model


async def test_high_risk_plan_requires_approval_and_routes_human(rt, infer, article):
    c = ctx("P-003")
    p = await design(
        c, await assess(c, infer, rt.policy, T0), ["sms", "email"], article, policy=rt.policy, templates=rt.templates, now=T0
    )
    assert (
        p.template_id == "SUPPORT_CALLBACK"
        and p.requires_approval
        and p.routes_to_human
        and p.expires_at == T0 + timedelta(hours=24)
    )


async def test_education_has_citation_and_idempotency_key_is_stable(rt, infer, article):
    c = ctx("P-007")
    c.prior_interventions = []
    r = await assess(c, infer, rt.policy, T0)
    kw = {"policy": rt.policy, "templates": rt.templates}
    a = await design(c, r, ["sms"], article, now=T0, **kw)
    b = await design(c, r, ["sms"], article, now=T0, **kw)
    later = await design(c, r, ["sms"], article, now=T0 + timedelta(days=1), **kw)
    assert a.citation and a.idempotency_key == b.idempotency_key != later.idempotency_key


async def test_least_intrusive_action_progression(rt, infer, article):
    c = ctx("P-007")
    c.events = [e for e in c.events if e.kind != "REFILL_LATE"]  # no late refills -> education first, then simple reminder
    c.prior_interventions = []
    r = await assess(c, infer, rt.policy, T0)
    kw = {"policy": rt.policy, "templates": rt.templates, "now": T0}
    assert (await design(c, r, ["sms"], article, **kw)).template_id == "EDUCATION_ROUTINE"
    c.prior_interventions = [PriorIntervention(template_id="EDUCATION_ROUTINE", sent_at=T0 - timedelta(days=20))]
    assert (await design(c, r, ["sms"], article, **kw)).template_id == "REMINDER_SIMPLE"


async def test_unconsented_channel_low_risk_and_language_produce_no_plan(rt, infer, article):
    kw = {"policy": rt.policy, "templates": rt.templates, "now": T0}
    c = ctx("P-003")
    with pytest.raises(NoIntervention) as e:
        await design(c, await assess(c, infer, rt.policy, T0), ["sms"], article, **kw)  # patient prefers email
    assert e.value.needs_human
    c1 = ctx("P-001")
    with pytest.raises(NoIntervention):
        await design(c1, await assess(c1, infer, rt.policy, T0), ["sms"], article, **kw)
    c2 = ctx("P-002")
    with pytest.raises(NoIntervention, match="language"):
        await design(c2, await assess(c2, infer, rt.policy, T0), ["sms", "email"], article, **kw)


async def test_policy_thresholds_come_from_config_not_code(rt, infer):
    c = ctx("P-002")
    base = (await assess(c, infer, rt.policy, T0)).band.value
    strict = rt.policy.model_copy(deep=True)
    strict.risk.band_cutoffs = {"medium": 0.01, "high": 0.02}
    assert base == "MEDIUM" and (await assess(c, infer, strict, T0)).band.value == "HIGH"
    assert dataclasses.is_dataclass(rt) and rt.policy.risk.status == "PLACEHOLDER_UNAPPROVED"
