# Risk register (residual risks; owners UNASSIGNED)
| ID | Risk | Status | Mitigation / next step |
|---|---|---|---|
| R1 | Identity is header-based placeholder (no SSO/MFA) | OPEN | Out of scope for this build; replace `app/deps.get_principal` with OIDC before any real use |
| R2 | Workflow store / memory / review tasks are in-memory (lost on restart); only the audit log is durable | OPEN | Swap Protocol implementations for encrypted DB/Redis |
| R3 | Regex detectors (English only) can miss paraphrases/other languages | OPEN | Held-out clinician-reviewed red-team set; multilingual review |
| R4 | Risk model is deterministic, uncalibrated; band cutoffs are placeholders | OPEN | Calibration + subgroup study on approved de-identified data |
| R5 | Template approvals and standing approval are synthetic demo records | OPEN | Real clinical sign-off recorded via `scripts/check_change_approvals.py --record` |
| R6 | Governance owners, DPIA sign-off, release record unsigned | OPEN | Organisation to assign (see `governance/`) |
| R7 | Delivery/MCP are simulated in-process | OPEN | Real MCP servers over authenticated transport |
| R8 | Audit log is single-writer; no external anchoring | OPEN | Multi-writer audit DB / periodic hash anchoring |
| R9 | No WCAG audit of the Streamlit UI; message readability only heuristic | OPEN | Accessibility review |
| R10 | Drift not measured (no approved baseline) | OPEN | Approve baseline, set `ADHERENCE_DRIFT_BASELINE_PATH` |
