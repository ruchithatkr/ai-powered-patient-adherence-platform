"""Distribution drift (population stability index) for the risk-band mix. Requires an APPROVED baseline; none is invented."""

import math
from collections.abc import Mapping


def psi(baseline: Mapping[str, float], current: Mapping[str, float], eps: float = 1e-4) -> float:
    keys = set(baseline) | set(current)
    b_tot, c_tot = sum(baseline.values()) or 1.0, sum(current.values()) or 1.0
    total = 0.0
    for k in keys:
        b = max(baseline.get(k, 0) / b_tot, eps)
        c = max(current.get(k, 0) / c_tot, eps)
        total += (c - b) * math.log(c / b)
    return total
