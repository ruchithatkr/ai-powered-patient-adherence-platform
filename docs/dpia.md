# DPIA / impact assessment (DRAFT - unsigned; synthetic data only)
Purpose: adherence reminders/education + risk-based routing to care teams. Lawful basis/jurisdiction: **UNDEFINED - organisation to set**.
Data: pseudonymous patient ref, medication label, schedule, adherence events, consent, preferences, first name (template field only). No diagnoses.
Minimisation: gateway returns only the fields above; free text is screened and never stored/logged; audit stores hashes.
Rights: consent withdrawal (`POST /patients/{ref}/withdraw-consent`), access/export, correction, deletion (legal-hold aware), retention (`POST /admin/retention`).
Residual risks: see `docs/risk-register.md`. Approval: **UNSIGNED**.
