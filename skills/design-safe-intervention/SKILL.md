# Skill: design-safe-intervention
Purpose: non-clinical, personalised adherence intervention from clinician-approved templates only.
Steps: least intrusive eligible action -> personalise within template fields -> frequency / quiet hours / accessibility /
wording checks -> approval when tier or action requires -> executable plan with rationale, expiry, idempotency key.
Output: `InterventionPlan`. Implementation: `skills/design_safe_intervention.py`. Templates: `domain/synthetic.py`.
Tests: `tests/test_skills.py`, `tests/test_guardrails.py`, `tests/test_workflow.py`.
