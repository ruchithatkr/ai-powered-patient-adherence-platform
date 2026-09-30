# Architecture and data flow
```
Care coordinator -> Streamlit UI (app/ui.py) -> FastAPI (app/api.py)
   -> adherence-supervisor (agents/supervisor.py, state machine in workflows/definition.py)
       intake -> authorize -> retrieve -> assess -> plan -> safety_check -> approval -> execute -> verify -> close
       sub-agents (agents/subagents.py) -> skills (skills/*.py) -> MCP gateway (mcp/gateway.py, allowlist servers.yaml)
       -> synthetic backends;  every hop -> append-only hash-chained audit (observability/audit.py)
Reviewer (pharmacist/nurse/clinician) -> POST /workflows/{id}/decision  (approval token minted only here or by recorded standing approval)
```
Data classification: all data is SYNTHETIC. In production, patient refs are pseudonymous; medication and adherence data are sensitive health information.
Workflow state: `memory/store.py` (TTL + optimistic locking). Long-term memory: consented outcomes only, namespaced by tenant+patient.
Prototype-only simplifications: in-memory stores, header-based identity, in-process "MCP", simulated notification delivery, deterministic
risk model (no LLM is called anywhere).

# Threat model (STRIDE summary)
| Threat | Control | Test |
|---|---|---|
| Spoofed identity / role | identity-context match + role checks (prod: SSO+MFA, NOT implemented) | test_workflow identity/role |
| Prompt injection via patient text or tool output | input guardrail; tool output treated as data; no LLM in decision path | test_malicious_or_phi_input_blocked |
| Unapproved external send | gateway requires valid approval token minted by approval flow | test_external_write_requires_valid_approval_token |
| Tampering with audit log | hash chain + append-only API | test_audit_refuses_phi_fields_and_detects_tampering |
| PHI leakage in logs | audit refuses free-text fields; stores hashes | same |
| Consent withdrawn mid-flight | consent re-checked before execute | test_consent_withdrawn_while_awaiting_approval |
| Self-approval | segregation of duties | test_requester_cannot_approve_own_and_wrong_role_denied |
| Duplicate/blind retry of sends | idempotency key; no retry on write failure + alert | test_tool_failure_stops_without_retry |
Residual risks: keyword detectors are bypassable; in-memory audit is not durable; no real auth; risk model unvalidated.

# Governance owners (placeholders - must be assigned by the organisation)
Product, clinical safety, privacy, security, model risk, data, operations: **UNASSIGNED**.

# ADR-001: Deterministic core, no LLM in the prototype
Permissions, consent, emergency and approval gates are deterministic per CLAUDE.md. Patient-facing text comes only from approved templates,
so an LLM adds no value yet and would add risk. An LLM gateway may later be added behind content-safety with human approval for free text.

# Model card: adherence-risk-deterministic-fallback 0.1.0
Intended use: prioritise adherence-support outreach for human-supervised teams. Excluded: diagnosis, dosing, eligibility, emergency triage.
Inputs: confirmed/missed dose reports, late refills. Excludes demographics and message-engagement. Thresholds are placeholders.
Calibration, subgroup performance and validity: NOT YET MEASURED.

# Runbooks
- **Tool outage**: workflow stops (`TOOL_UNAVAILABLE`), alert event recorded; writes are never auto-retried. Restore tool, re-submit workflow (idempotency key prevents duplicates).
- **Stale data**: workflow stops (`STALE_MEDICATION_DATA`); ask care team to re-verify the medication plan.
- **Model failure**: stop; deterministic fallback is the model; escalate to model-risk owner.
- **Adverse-event/emergency language**: approved safety guidance shown; review task created for trained staff; no autonomous emergency call.
- **Incident**: preserve audit trace (`GET /workflows/{id}/trace`), notify accountable owners, roll back (below).


# Update 1.1 (refactor)
- Composition root `app/container.py`; FastAPI factory `create_app(runtime)`; async supervisor engine (`agents/supervisor.py`) driving stage handlers (`workflows/stages.py`).
- Config as data: `workflows/policy.yaml`, `workflows/templates.yaml` (locale-aware, DRAFT locales unusable), `guardrails/patterns.yaml`, `mcp/servers.yaml` (JSON-Schema contracts).
- MCP gateway: schema-validated params/responses, destination allowlist, consent gating, approval tokens bound to one idempotency key + expiry, real timeouts, untrusted-content screening.
- Observability: OTel span ids -> audit `span_id/parent_span_id`; durable hash-chained JSONL; metrics (`/metrics`), alerts (`/alerts`), drift (needs approved baseline), dashboard tab + `observability/dashboards/adherence.json`.
- Not implemented (by request): authentication (header placeholder) and a real database.
