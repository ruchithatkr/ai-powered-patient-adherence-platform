"""Patient-facing message accessibility checks (reading level, length, shouting)."""

import re

from domain.models import GuardrailResult

SMS_KEYWORDS = {"HELP", "STOP"}  # conventional reply keywords are not "shouting"


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith("le") and n > 1:
        n -= 1
    return max(1, n)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid grade level (English heuristic)."""
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if not sentences or not words:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return 0.39 * len(words) / len(sentences) + 11.8 * syl / len(words) - 15.59


def check_accessibility(body: str, max_grade: float, max_chars: int, channel: str) -> GuardrailResult:
    problems: list[str] = []
    grade = reading_grade(body)
    if grade > max_grade:
        problems.append(f"reading grade {grade:.1f} > {max_grade}")
    if channel == "sms" and len(body) > max_chars:
        problems.append(f"{len(body)} chars > {max_chars}")
    shouting = [w for w in re.findall(r"\b[A-Z]{4,}\b", body) if w not in SMS_KEYWORDS]
    if shouting:
        problems.append("all-caps words")
    if problems:
        return GuardrailResult(guardrail="accessibility", decision="BLOCK", detail="; ".join(problems))
    return GuardrailResult(guardrail="accessibility", decision="PASS")
