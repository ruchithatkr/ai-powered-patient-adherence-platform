"""Typed loader for workflows/policy.yaml. Every audit event carries policy_version; the file digest is recorded at startup."""

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel


class Freshness(BaseModel):
    medication_plan_max_age_days: int


class RiskPolicy(BaseModel):
    status: str
    min_observations: int
    expected_observations: int
    min_confidence: float
    band_cutoffs: dict[str, float]


class FrequencyCap(BaseModel):
    max_messages: int
    window_days: int


class InterventionPolicy(BaseModel):
    frequency_cap: FrequencyCap
    plan_expiry_hours: int
    blocking_outcomes: list[str]


class AccessibilityPolicy(BaseModel):
    status: str
    max_reading_grade: float
    max_sms_chars: int


class OperationsPolicy(BaseModel):
    workflow_ttl_hours: int
    memory_retention_days: int
    tool_timeout_seconds: float
    notification_rate_limit_per_minute: int


class AlertPolicy(BaseModel):
    repeated_tool_failures: dict[str, int]
    anomalous_access: dict[str, int]
    drift_psi: float


class Policy(BaseModel):
    policy_version: str
    workflow_version: str
    freshness: Freshness
    risk: RiskPolicy
    intervention: InterventionPolicy
    accessibility: AccessibilityPolicy
    operations: OperationsPolicy
    alerts: AlertPolicy
    emergency_guidance: str
    digest: str = ""

    @classmethod
    def load(cls, path: Path) -> "Policy":
        raw = path.read_bytes()
        policy = cls(**yaml.safe_load(raw))
        policy.digest = hashlib.sha256(raw).hexdigest()[:16]
        return policy
