"""Metric scorers (pure Python). Used on labelled, de-identified datasets once they exist; unit-tested on toy inputs."""

from collections.abc import Mapping, Sequence


def brier(y: Sequence[int], p: Sequence[float]) -> float:
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p, strict=True)) / len(y)


def ece(y: Sequence[int], p: Sequence[float], bins: int = 10) -> float:
    """Expected calibration error with equal-width bins."""
    n, total = len(y), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, pi in enumerate(p) if (lo <= pi < hi) or (b == bins - 1 and pi == 1.0)]
        if idx:
            conf = sum(p[i] for i in idx) / len(idx)
            acc = sum(y[i] for i in idx) / len(idx)
            total += len(idx) / n * abs(conf - acc)
    return total


def auroc(y: Sequence[int], p: Sequence[float]) -> float:
    """Rank-based AUROC (ties count half)."""
    pos = [pi for yi, pi in zip(y, p, strict=True) if yi == 1]
    neg = [pi for yi, pi in zip(y, p, strict=True) if yi == 0]
    if not pos or not neg:
        raise ValueError("AUROC needs both classes")
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def auprc(y: Sequence[int], p: Sequence[float]) -> float:
    """Average precision."""
    order = sorted(range(len(y)), key=lambda i: -p[i])
    tp, ap, total_pos = 0, 0.0, sum(y)
    if total_pos == 0:
        raise ValueError("AUPRC needs positives")
    for rank, i in enumerate(order, 1):
        if y[i] == 1:
            tp += 1
            ap += tp / rank
    return ap / total_pos


def sensitivity_specificity(y: Sequence[int], p: Sequence[float], threshold: float) -> tuple[float, float]:
    tp = sum(1 for yi, pi in zip(y, p, strict=True) if yi == 1 and pi >= threshold)
    fn = sum(1 for yi, pi in zip(y, p, strict=True) if yi == 1 and pi < threshold)
    tn = sum(1 for yi, pi in zip(y, p, strict=True) if yi == 0 and pi < threshold)
    fp = sum(1 for yi, pi in zip(y, p, strict=True) if yi == 0 and pi >= threshold)
    return (tp / (tp + fn) if tp + fn else float("nan"), tn / (tn + fp) if tn + fp else float("nan"))


def subgroup_gap(y: Sequence[int], p: Sequence[float], groups: Sequence[str], metric: str = "ece") -> Mapping[str, float]:
    """Per-cohort metric and the max pairwise gap (key '__max_gap__'). Cohorts must be approved by governance."""
    fn = {"ece": ece, "brier": brier}[metric]
    out: dict[str, float] = {}
    for g in sorted(set(groups)):
        idx = [i for i, gi in enumerate(groups) if gi == g]
        out[g] = fn([y[i] for i in idx], [p[i] for i in idx])
    out["__max_gap__"] = max(out.values()) - min(out.values()) if out else 0.0
    return out
