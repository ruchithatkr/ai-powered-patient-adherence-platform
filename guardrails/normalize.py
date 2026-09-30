"""Text normalisation so detectors resist case, spacing, zero-width, leetspeak and homoglyph obfuscation."""

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_LEET = str.maketrans("013457@$", "oieastas")
# Common Cyrillic/Greek look-alikes -> Latin
_CONFUSABLES = str.maketrans("аеорсхіуѕкмтнвαοεικ", "aeopcxiyskmthbaoeik")
_SPACED_LETTERS = re.compile(r"(?<=\b\w)[\s.\-_*]+(?=\w\b)")


def base(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH)
    t = t.casefold().translate(_CONFUSABLES)
    return re.sub(r"\s+", " ", t).strip()


def variants(text: str) -> list[str]:
    """Normalised variants used for language-based detectors (not for digit-based PHI/secret detectors)."""
    b = base(text)
    return list(dict.fromkeys([b, b.translate(_LEET), _SPACED_LETTERS.sub("", b), _SPACED_LETTERS.sub("", b.translate(_LEET))]))
