"""Tiny in-process metrics registry with Prometheus text exposition. Labels are low-cardinality and never carry identifiers."""

import threading
from collections import defaultdict
from typing import Any

BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500)
LabelKey = tuple[tuple[str, str], ...]


def _key(labels: dict[str, str]) -> LabelKey:
    return tuple(sorted(labels.items()))


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[LabelKey, float]] = defaultdict(lambda: defaultdict(float))
        self._hist: dict[str, dict[LabelKey, list[float]]] = defaultdict(lambda: defaultdict(list))

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        with self._lock:
            self._counters[name][_key(labels)] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._hist[name][_key(labels)].append(value)

    def counter(self, name: str, **labels: str) -> float:
        with self._lock:
            series = self._counters.get(name, {})
            want = set(labels.items())
            return sum(v for k, v in series.items() if want <= set(k))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {"counters": {}, "histograms": {}}
            for name, series in self._counters.items():
                out["counters"][name] = [{"labels": dict(k), "value": v} for k, v in series.items()]
            for name, hs in self._hist.items():
                out["histograms"][name] = [
                    {"labels": dict(k), "count": len(v), "sum": sum(v), "p95": _p95(v)} for k, v in hs.items()
                ]
            return out

    def prometheus(self, extra_gauges: dict[str, float] | None = None) -> str:
        lines: list[str] = []
        with self._lock:
            for name, series in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                for k, v in series.items():
                    lines.append(f"{name}{_fmt(k)} {v}")
            for name, hs in sorted(self._hist.items()):
                lines.append(f"# TYPE {name} histogram")
                for k, vals in hs.items():
                    for b in BUCKETS:
                        lines.append(f"{name}_bucket{_fmt(k, le=str(b))} {sum(1 for x in vals if x <= b)}")
                    lines.append(f"{name}_bucket{_fmt(k, le='+Inf')} {len(vals)}")
                    lines.append(f"{name}_sum{_fmt(k)} {sum(vals)}")
                    lines.append(f"{name}_count{_fmt(k)} {len(vals)}")
        for name, val in sorted((extra_gauges or {}).items()):
            lines += [f"# TYPE {name} gauge", f"{name} {val}"]
        return "\n".join(lines) + "\n"


def _p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def _fmt(key: LabelKey, **extra: str) -> str:
    items = list(key) + list(extra.items())
    return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}" if items else ""
