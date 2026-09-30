# Skill: assess-adherence-risk
Purpose: grounded risk assessment from authorized, current evidence.
Inputs: pseudonymous patient ref, consent scope, medication-plan version, adherence events, evaluation timestamp.
Steps: validate (schema/auth/freshness/min data) -> allowed features only -> approved model + confidence/missingness ->
non-causal factor explanations -> uncertainty and escalation thresholds (ABSTAIN routes to a human).
Output: `RiskAssessment` (see `domain/models.py`). Implementation: `skills/assess_adherence_risk.py`.
Fixtures: synthetic patients in `domain/synthetic.py` (P-001 LOW, P-002 MEDIUM, P-003 HIGH, P-004 ABSTAIN, P-006 stale).
Tests: `tests/test_skills.py`, `tests/test_workflow.py`.
