"""Deterministic guardrails. They fail closed and never depend on an LLM."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from domain.models import Consent, GuardrailResult, InterventionPlan, Preferences, PriorIntervention

INJECTION = [r"ignore (all |any )?(previous|prior|above) (instructions|rules)", r"disregard .{0,30}(policy|rules|instructions)",
             r"you are now", r"system prompt", r"reveal .{0,20}(prompt|secret|key)", r"override (the )?(guardrail|policy|approval)",
             r"skip (the )?approval"]
SECRETS = [r"sk-[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{16}", r"(?i)bearer\s+[a-z0-9\-_.]{20,}", r"(?i)password\s*[:=]"]
PHI = [r"\b\d{3}-\d{2}-\d{4}\b", r"[\w.+-]+@[\w-]+\.[\w.]+", r"\b\+?\d[\d\s().-]{8,}\d\b"]
EMERGENCY = [r"chest pain", r"can'?t breathe|cannot breathe|difficulty breathing", r"overdose", r"suicid", r"unconscious",
             r"allergic reaction|anaphyla", r"swelling of (the )?(face|throat)", r"severe (bleeding|pain)", r"side effect", r"adverse"]
UNSAFE_CONTENT = [r"\b(increase|decrease|double|halve|reduce|skip|stop|discontinue)\b.{0,20}\b(dose|dosage|medicine|medication|pill)",
                  r"\byou (have|likely have|probably have)\b", r"\bdiagnos", r"\bprescrib", r"\b(guarantee|cure[sd]?)\b",
                  r"\b(buy|discount|limited offer|promo)\b", r"\b(you must|or else|irresponsible|ashamed|lazy)\b"]


def _any(patterns, text):
    return [p for p in patterns if re.search(p, text or "", re.I)]


def check_input(text: str) -> list[GuardrailResult]:
    out = []
    if _any(INJECTION, text):
        out.append(GuardrailResult(guardrail="prompt-injection", decision="BLOCK", detail="injection pattern"))
    if _any(SECRETS, text):
        out.append(GuardrailResult(guardrail="secret-detect", decision="BLOCK", detail="secret-like string"))
    if _any(PHI, text):
        out.append(GuardrailResult(guardrail="phi-detect", decision="BLOCK", detail="direct identifier in free text"))
    if _any(EMERGENCY, text):
        out.append(GuardrailResult(guardrail="emergency-language", decision="REVIEW",
                                   detail="potential emergency/adverse-event language -> trained staff"))
    return out or [GuardrailResult(guardrail="input", decision="PASS")]


def is_emergency(text: str) -> bool:
    return bool(_any(EMERGENCY, text))


def check_content(body: str, allowed_body_prefix_check: bool = True) -> GuardrailResult:
    hits = _any(UNSAFE_CONTENT, body)
    if hits:
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


def consent_decision(consent: Consent | None, purpose: str, channel: str | None, now_utc: datetime):
    """Returns (Decision, policy_id, reason). Never modifies consent."""
    if consent is None:
        return "DENY", "CONSENT-POL-1", "no consent record"
    if consent.withdrawn:
        return "DENY", consent.policy_id, "consent withdrawn"
    if consent.expires_at < now_utc:
        return "DENY", consent.policy_id, "consent expired"
    if purpose not in consent.purposes:
        return "DENY", consent.policy_id, "purpose not in consent scope"
    if channel and channel not in consent.channels:
        return "REVIEW", consent.policy_id, "channel not consented"
    return "ALLOW", consent.policy_id, "ok"
