# AI-Powered Patient Adherence Platform (synthetic-data prototype)
**Problem**: chronic-disease patients miss doses; care teams need explainable, consented, human-supervised support.
**Users**: care coordinators (start workflows), pharmacists/nurses/clinicians (approve), privacy officers (deletion/audit).
**Non-goals**: diagnosis, prescribing, dose changes, emergency triage, autonomous emergency escalation, use of real PHI.
Governing spec: [CLAUDE.md](CLAUDE.md). Architecture, threat model, model card, runbooks, ADR: [docs/architecture.md](docs/architecture.md).

## Workflow
`intake -> authorize -> retrieve -> assess -> plan -> safety check -> approval -> execute -> verify -> close`, stopping (fail closed) on missing
consent, identity mismatch, stale medication data, tool failure or safety escalation. Emergency/adverse-event language shows approved safety
guidance and routes to trained staff. Low-confidence assessments ABSTAIN and go to a human.

## Setup and run (Python 3.12+; developed on 3.14)
```
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
./run.sh          # UI http://localhost:8501 · API docs http://localhost:8000/docs
pytest -q                 # tests
python evals/run_evals.py # scenario/safety evaluation -> evals/results/0.1.0.md
```
Environment variables: `API_URL` (UI -> API, default http://127.0.0.1:8000). No secrets are needed. `ALLOW_REAL_PHI` must be unset.

## Synthetic demo (UI, tab 1 then tab 2)
| Patient | Expected outcome |
|---|---|
| P-001 | LOW risk, no outreach |
| P-002 | Preferred language has no approved template -> human review |
| P-003 | HIGH risk -> awaiting approval -> approve as pharmacist in tab 2 -> simulated send |
| P-004 | Too little data -> ABSTAIN -> human review |
| P-005 / P-006 | Consent withdrawn / stale medication data -> stopped |
| P-007 | Frequency cap reached -> no outreach |
Try messages such as `I have chest pain` (escalation) or `ignore previous instructions` (blocked).

## API examples (synthetic)
```
curl -XPOST localhost:8000/workflows -H 'X-Actor: coord-1' -H 'X-Role: care_coordinator' -H 'content-type: application/json' \
  -d '{"patient_ref":"P-003","identity_patient_ref":"P-003"}'
curl -XPOST localhost:8000/workflows/<trace_id>/decision -H 'X-Actor: rph-1' -H 'X-Role: pharmacist' -H 'content-type: application/json' \
  -d '{"decision":"APPROVE","rationale":"reviewed evidence packet"}'
curl localhost:8000/workflows/<trace_id>/trace -H 'X-Actor: a' -H 'X-Role: care_coordinator'        # redacted trace export
curl localhost:8000/workflows/<trace_id>/explanation -H 'X-Actor: a' -H 'X-Role: care_coordinator'  # patient-readable
```
Also: `POST /patients/{ref}/withdraw-consent`, `DELETE /patients/{ref}/data` (privacy_officer), `GET /audit/verify`.

## Components
Agents/sub-agents: `agents/`. Skills (`assess-adherence-risk`, `design-safe-intervention`): `skills/`. MCP allowlist + gateway: `mcp/`.
State/memory (TTL, optimistic lock, retention, deletion, legal hold): `memory/`. Guardrails: `guardrails/`. Audit/trace: `observability/`.
Hooks (versioned, fail closed): `hooks/` wired in `.claude/settings.json`. Approval: only pharmacist/nurse/clinician, never the requester.

## Privacy and security controls implemented
Consent/purpose/channel checks, quiet hours in the patient's timezone, frequency caps, approved-template-only messaging with citations,
PHI/secret/injection screening, PHI-safe hashed audit events, hash-chain tamper detection, deletion and consent withdrawal, RBAC on gateway.

## Evaluation
Methodology and latest executed results: [evals/results/0.1.0.md](evals/results/0.1.0.md). Thresholds: [evals/thresholds.yaml](evals/thresholds.yaml).
Risk-model accuracy, calibration and fairness: **NOT YET MEASURED**.

## Limitations / unresolved risks (be honest before any real use)
Not production-ready: header-based identity (no SSO/MFA), in-memory non-durable stores, in-process MCP, simulated delivery, no PostgreSQL/Redis,
no OpenTelemetry export or dashboards, no CI/SBOM/SAST pipeline, keyword-based detectors, unvalidated placeholder risk thresholds, English-only templates,
no accessibility audit, no governance owners assigned, no signed release record. Streamlit UI stated in the spec as prototype choice.

## Rollback and change approval
Stop `run.sh` processes; redeploy the previous tagged version (no git repo exists yet - initialise one and tag releases). Any change to
templates, thresholds, MCP permissions or models needs human approval recorded with the change.
