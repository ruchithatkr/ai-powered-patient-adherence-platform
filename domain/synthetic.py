"""Synthetic-only data. No real patient data exists in this repository."""

from datetime import datetime, timedelta
from typing import Any

from domain.models import (
    AdherenceEvent,
    Consent,
    MedicationPlan,
    PatientContext,
    Preferences,
    PriorIntervention,
)

FIRST_NAMES = {
    "P-001": "Alex",
    "P-002": "Sam",
    "P-003": "Jordan",
    "P-004": "Riley",
    "P-005": "Casey",
    "P-006": "Morgan",
    "P-007": "Taylor",
}


def _events(now: datetime, confirmed: int, missed: int, late: list[int]) -> list[AdherenceEvent]:
    ev = [AdherenceEvent(kind="DOSE_CONFIRMED", at=now - timedelta(days=i + 1)) for i in range(confirmed)]
    ev += [AdherenceEvent(kind="DOSE_MISSED_REPORTED", at=now - timedelta(days=i + 1, hours=1)) for i in range(missed)]
    ev += [AdherenceEvent(kind="REFILL_LATE", at=now - timedelta(days=3), days_late=d) for d in late]
    return ev


def build(now: datetime) -> dict[str, dict[str, Any]]:
    def patient(
        ref: str,
        label: str,
        times: list[str],
        conf: int,
        miss: int,
        late: list[int],
        prefs: Preferences,
        consent_kw: dict[str, Any] | None = None,
        plan_age: int = 2,
        prior: list[PriorIntervention] | None = None,
    ) -> dict[str, Any]:
        c: dict[str, Any] = {
            "purposes": ["adherence_support"],
            "channels": ["sms", "email"],
            "expires_at": now + timedelta(days=200),
        }
        c.update(consent_kw or {})
        return {
            "consent": Consent(patient_ref=ref, **c),
            "context": PatientContext(
                patient_ref=ref,
                first_name=FIRST_NAMES[ref],
                plan=MedicationPlan(
                    version="v3",
                    medication_label=label,
                    schedule_times_local=times,
                    verified_at=now - timedelta(days=plan_age),
                ),
                prefs=prefs,
                events=_events(now, conf, miss, late),
                prior_interventions=prior or [],
                provenance=["synthetic-patient-records@v3", "synthetic-adherence-events@2026"],
            ),
        }

    return {
        # Consistent adherence -> LOW
        "P-001": patient(
            "P-001",
            "Blood-pressure medicine (morning)",
            ["08:00"],
            13,
            1,
            [],
            Preferences(timezone="America/New_York", preferred_channel="sms"),
            prior=[PriorIntervention(template_id="REMINDER_SIMPLE", sent_at=now - timedelta(days=30))],
        ),
        # Mixed -> MEDIUM (Spanish speaker: no approved Spanish template -> human review)
        "P-002": patient(
            "P-002",
            "Diabetes medicine (twice daily)",
            ["08:00", "20:00"],
            9,
            5,
            [3],
            Preferences(timezone="Europe/London", language="es", large_text=True),
        ),
        # Poor -> HIGH
        "P-003": patient(
            "P-003",
            "Heart medicine (evening)",
            ["21:00"],
            4,
            10,
            [9, 12],
            Preferences(timezone="Asia/Kolkata", preferred_channel="email"),
        ),
        # Too little data -> ABSTAIN -> human review
        "P-004": patient("P-004", "Asthma controller", ["07:30"], 2, 0, [], Preferences()),
        # Consent withdrawn -> STOP
        "P-005": patient(
            "P-005",
            "Cholesterol medicine",
            ["19:00"],
            10,
            3,
            [],
            Preferences(),
            consent_kw={"withdrawn": True},
        ),
        # Stale medication data -> STOP
        "P-006": patient("P-006", "Thyroid medicine", ["06:30"], 8, 4, [], Preferences(), plan_age=90),
        # Frequency cap reached (3 outreach in 7 days)
        "P-007": patient(
            "P-007",
            "Blood-thinner reminder",
            ["09:00"],
            8,
            6,
            [4],
            Preferences(),
            prior=[PriorIntervention(template_id="REMINDER_SIMPLE", sent_at=now - timedelta(days=d)) for d in (1, 3, 5)],
        ),
    }
