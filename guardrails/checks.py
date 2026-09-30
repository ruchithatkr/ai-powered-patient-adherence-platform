"""Deterministic guardrails. They fail closed and never depend on an LLM."""

import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from domain.models import Consent, GuardrailResult, Preferences, PriorIntervention
from guardrails.normalize import base, variants

PATTERNS_PATH = Path(__file__).with_name("patterns.yaml")
LANGUAGE_CATEGORIES = {"injection", "emergency", "unsafe_content"}


@lru_cache(maxsize=1)
def _load() -> tuple[str, dict[str, list[re.Pattern[str]]]]:
    raw: dict[str, Any] = yaml.safe_load(PATTERNS_PATH.read_text())
    version = str(raw.pop("version"))
    return version, {k: [re.compile(p, re.I) for p in v] for k, v in raw.items()}


def patterns_version() -> str:
    return _load()[0]


def _hit(category: str, text: str | None) -> bool:
    pats = _load()[1][category]
    candidates = variants(text or "") if category in LANGUAGE_CATEGORIES else [base(text or "")]
    return any(p.search(c) for p in pats for c in candidates)


def check_input(text: str) -> list[GuardrailResult]:
    out: list[GuardrailResult] = []
    if _hit("injection", text):
        out.append(GuardrailResult(guardrail="prompt-injection", decision="BLOCK", detail="injection pattern"))
    if _hit("secrets", text):
        out.append(GuardrailResult(guardrail="secret-detect", decision="BLOCK", detail="secret-like string"))
    if _hit("phi", text):
        out.append(GuardrailResult(guardrail="phi-detect", decision="BLOCK", detail="direct identifier in free text"))
    if _hit("emergency", text):
        out.append(
            GuardrailResult(
                guardrail="emergency-language",
                decision="REVIEW",
                detail="potential emergency/adverse-event language -> trained staff",
            )
        )
    return out or [GuardrailResult(guardrail="input", decision="PASS")]


def is_emergency(text: str) -> bool:
    return _hit("emergency", text)


def contains_injection(text: str) -> bool:
    """Used on tool output, which is untrusted DATA and must never carry instructions."""
    return _hit("injection", text)


def contains_sensitive(text: str) -> str | None:
    """Return 'secret' / 'phi' if a string looks like one, so loggers can redact it."""
    if _hit("secrets", text):
        return "secret"
    if _hit("phi", text):
        return "phi"
    return None


def check_content(body: str) -> GuardrailResult:
    if _hit("unsafe_content", body):
        return GuardrailResult(guardrail="content-safety", decision="BLOCK", detail="unsafe wording detected")
    return GuardrailResult(guardrail="content-safety", decision="PASS")


def in_quiet_hours(prefs: Preferences, now_utc: datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(prefs.timezone))
    s, e = prefs.quiet_start_hour, prefs.quiet_end_hour
    return (local.hour >= s or local.hour < e) if s > e else (s <= local.hour < e)


def next_allowed_time(prefs: Preferences, now_utc: datetime) -> datetime:
    t = now_utc
    for _ in range(48):
        if not in_quiet_hours(prefs, t):
            return t
        t += timedelta(minutes=30)
    return t


def frequency_ok(prior: list[PriorIntervention], now_utc: datetime, cap: int = 3, days: int = 7) -> bool:
    return sum(1 for p in prior if now_utc - p.sent_at <= timedelta(days=days)) < cap


def consent_decision(consent: Consent | None, purpose: str, channel: str | None, now_utc: datetime) -> tuple[str, str, str]:
    """Returns (ALLOW|DENY|REVIEW, policy_id, reason). Never modifies consent."""
    if consent is None:
        return "DENY", "CONSENT-POL-1", "no consent record"
    if consent.withdrawn:
        return "DENY", consent.policy_id, "consent withdrawn"
    if consent.expires_at < now_utc:
        return "DENY", consent.policy_id, "consent expired"
    if purpose not in consent.purposes and purpose != "safety_review":
        return "DENY", consent.policy_id, "purpose not in consent scope"
    if channel and channel not in consent.channels:
        return "REVIEW", consent.policy_id, "channel not consented"
    return "ALLOW", consent.policy_id, "ok"
