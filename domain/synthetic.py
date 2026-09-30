"""Synthetic-only data. No real patient data exists in this repository."""
from datetime import datetime, timedelta, timezone

from domain.models import (AdherenceEvent, Consent, MedicationPlan, PatientContext,
                           Preferences, PriorIntervention)

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _events(now, confirmed, missed, late):
    ev = []
    for i in range(confirmed):
        ev.append(AdherenceEvent(kind="DOSE_CONFIRMED", at=now - timedelta(days=i + 1)))
    for i in range(missed):
        ev.append(AdherenceEvent(kind="DOSE_MISSED_REPORTED", at=now - timedelta(days=i + 1, hours=1)))
    for d in late:
        ev.append(AdherenceEvent(kind="REFILL_LATE", at=now - timedelta(days=3), days_late=d))
    return ev


def build():
    now = NOW()

    def patient(ref, label, times, conf, miss, late, prefs, consent_kw=None, plan_age=2, prior=None):
        c = dict(purposes=["adherence_support"], channels=["sms", "email"], expires_at=now + timedelta(days=200))
        c.update(consent_kw or {})
        return dict(
            consent=Consent(patient_ref=ref, **c),
            context=PatientContext(
                patient_ref=ref,
                plan=MedicationPlan(version="v3", medication_label=label, schedule_times_local=times,
                                    verified_at=now - timedelta(days=plan_age)),
                prefs=prefs, events=_events(now, conf, miss, late),
                prior_interventions=prior or [],
                provenance=["synthetic-patient-records@v3", "synthetic-adherence-events@2026"]),
        )

    return {
        # Consistent adherence -> LOW
        "P-001": patient("P-001", "Blood-pressure medicine (morning)", ["08:00"], 13, 1, [],
                         Preferences(timezone="America/New_York", preferred_channel="sms"),
                         prior=[PriorIntervention(template_id="REMINDER_SIMPLE", sent_at=now - timedelta(days=30))]),
        # Mixed -> MEDIUM
        "P-002": patient("P-002", "Diabetes medicine (twice daily)", ["08:00", "20:00"], 9, 5, [3],
                         Preferences(timezone="Europe/London", language="es", large_text=True)),
        # Poor -> HIGH
        "P-003": patient("P-003", "Heart medicine (evening)", ["21:00"], 4, 10, [9, 12],
                         Preferences(timezone="Asia/Kolkata", preferred_channel="email")),
        # Too little data -> ABSTAIN -> human review
        "P-004": patient("P-004", "Asthma controller", ["07:30"], 2, 0, [], Preferences()),
        # Consent withdrawn -> STOP
        "P-005": patient("P-005", "Cholesterol medicine", ["19:00"], 10, 3, [], Preferences(),
                         consent_kw=dict(withdrawn=True)),
        # Stale medication data -> STOP
        "P-006": patient("P-006", "Thyroid medicine", ["06:30"], 8, 4, [], Preferences(), plan_age=90),
        # Frequency cap reached (3 outreach in 7 days)
        "P-007": patient("P-007", "Blood-thinner reminder", ["09:00"], 8, 6, [4], Preferences(),
                         prior=[PriorIntervention(template_id="REMINDER_SIMPLE", sent_at=now - timedelta(days=d))
                                for d in (1, 3, 5)]),
    }


# Verified knowledge base (synthetic stand-in for an approved medication-education source).
KNOWLEDGE_BASE = {
    "KB-ADH-001": {"version": "2026.1", "title": "Building a routine for taking medicines",
                   "text": "Linking your medicine to a daily habit, such as brushing teeth, can make it easier to remember.",
                   "source": "Synthetic Patient Education Library v2026.1"},
    "KB-REFILL-002": {"version": "2026.1", "title": "Refilling on time",
                      "text": "Ask your pharmacy about reminders so a refill is ready before you run out.",
                      "source": "Synthetic Patient Education Library v2026.1"},
}

# Clinician-approved templates (only these may be sent). Fields in {braces} are the only personalisation.
TEMPLATES = {
    "REMINDER_SIMPLE": {"version": "1", "body": "Hello {first_name}, this is a friendly reminder about your scheduled {medication_label} at {time}. Reply HELP to reach your care team.",
                        "kb": None, "high_touch": False},
    "EDUCATION_ROUTINE": {"version": "1", "body": "Hello {first_name}, a tip that helps many people: {kb_text} Reply HELP to reach your care team.",
                          "kb": "KB-ADH-001", "high_touch": False},
    "EDUCATION_REFILL": {"version": "1", "body": "Hello {first_name}, {kb_text} Reply HELP to reach your care team.",
                         "kb": "KB-REFILL-002", "high_touch": False},
    "SUPPORT_CALLBACK": {"version": "1", "body": "Hello {first_name}, your care team would like to check in with you. Someone will contact you at a time that suits you.",
                         "kb": None, "high_touch": True},
}

EMERGENCY_GUIDANCE = ("If you think you are having a medical emergency, call your local emergency number now. "
                      "For side effects or other concerns, please contact your pharmacist or care team. "
                      "This service cannot give medical advice.")

FIRST_NAMES = {"P-001": "Alex", "P-002": "Sam", "P-003": "Jordan", "P-004": "Riley",
               "P-005": "Casey", "P-006": "Morgan", "P-007": "Taylor"}
