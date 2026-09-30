"""Typed domain models. All data is synthetic in local development."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class RiskBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    ABSTAIN = "ABSTAIN"  # low confidence -> human review


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REVIEW = "REVIEW"


class Status(StrEnum):
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ESCALATED = "ESCALATED"  # routed to trained staff
    STOPPED = "STOPPED"  # fail-closed stop condition
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"  # human rejected


class Consent(BaseModel):
    patient_ref: str
    purposes: list[str]
    channels: list[str]
    expires_at: datetime
    withdrawn: bool = False
    policy_id: str = "CONSENT-POL-1"


class MedicationPlan(BaseModel):
    version: str
    medication_label: str  # display label only; no dosing logic is derived from it
    verified_at: datetime
    schedule_times_local: list[str]
    source: str = "synthetic-patient-records"


class Preferences(BaseModel):
    language: str = "en"
    timezone: str = "UTC"
    quiet_start_hour: int = 21
    quiet_end_hour: int = 8
    large_text: bool = False
    preferred_channel: str = "sms"
    verified_timezone: bool = True


class AdherenceEvent(BaseModel):
    kind: Literal["DOSE_CONFIRMED", "DOSE_MISSED_REPORTED", "REFILL_LATE"]
    at: datetime
    days_late: int = 0
    # NOTE: message delivery / opens are never recorded as adherence events.


class PriorIntervention(BaseModel):
    template_id: str
    sent_at: datetime
    outcome: str = "unknown"


class PatientContext(BaseModel):
    patient_ref: str
    first_name: str = ""  # approved template field only; never logged
    plan: MedicationPlan
    prefs: Preferences
    events: list[AdherenceEvent]
    prior_interventions: list[PriorIntervention]
    provenance: list[str]


class Factor(BaseModel):
    name: str
    detail: str  # plain language, no causal claims
    evidence_id: str


class RiskAssessment(BaseModel):
    patient_ref: str
    score: float = Field(ge=0, le=1)
    band: RiskBand
    confidence: float = Field(ge=0, le=1)
    missingness: float = Field(ge=0, le=1)
    factors: list[Factor]
    model: str
    model_version: str
    calibrated: bool = False
    limitations: list[str]
    evidence_refs: list[str]
    recommended_next_step: str
    evaluated_at: datetime = Field(default_factory=utcnow)


class InterventionPlan(BaseModel):
    template_id: str
    template_version: str
    channel: str
    body: str  # rendered from approved template only
    citation: str | None = None
    requires_approval: bool
    approval_reasons: list[str]
    rationale: str
    idempotency_key: str
    expires_at: datetime
    evidence_refs: list[str]
    routes_to_human: bool = False
    send_not_before: datetime | None = None
    accessibility: list[str] = []
    language: str = "en"
    template_fields: dict[str, str] = {}


class Approval(BaseModel):
    approval_id: str
    approver: str
    role: str
    decision: Literal["APPROVE", "REJECT"]
    rationale: str
    conditions: str = ""
    at: datetime = Field(default_factory=utcnow)


class ActionReceipt(BaseModel):
    receipt_id: str
    tool: str
    idempotency_key: str
    delivery_status: str  # delivery only; never implies engagement or intake
    simulated: bool = True
    at: datetime = Field(default_factory=utcnow)


class GuardrailResult(BaseModel):
    guardrail: str
    decision: Literal["PASS", "BLOCK", "REVIEW"]
    detail: str = ""
    policy_version: str = "POLICY-1.0"


class WorkflowState(BaseModel):
    trace_id: str
    request_id: str
    patient_ref: str
    purpose: str
    requester: str
    requester_role: str
    consent_snapshot: Consent | None = None
    medication_plan_version: str | None = None
    evidence_refs: list[str] = []
    risk_assessment: RiskAssessment | None = None
    intervention_plan: InterventionPlan | None = None
    guardrail_results: list[GuardrailResult] = []
    approval: Approval | None = None
    action_receipt: ActionReceipt | None = None
    review_task_id: str | None = None
    status: Status = Status.RUNNING
    stage: str = "intake"
    stop_reason: str | None = None
    stages_completed: list[str] = []
    rationale: str = ""
    version: int = 0  # optimistic locking
    stages_visited: list[str] = []
    root_span_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
