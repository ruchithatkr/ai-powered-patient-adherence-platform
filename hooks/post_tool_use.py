#!/usr/bin/env python3
"""PostToolUse: validate response schema, redact logs, capture latency/status, store evidence hash."""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jsonschema import Draft202012Validator  # noqa: E402

from guardrails.checks import contains_injection, contains_sensitive  # noqa: E402
from hooks._common import digest, mcp_config, parse_mcp_tool, read_input, record  # noqa: E402


def _strings(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, list):
        return [s for x in v for s in _strings(x)]
    return []


def main() -> int:
    data = read_input()
    name = str(data.get("tool_name", ""))
    resp = data.get("tool_response")
    strings = _strings(resp)
    fields: dict[str, Any] = {
        "tool": name,
        "evidence_hash": digest(resp),  # hash only; raw response is never stored
        "latency_ms": data.get("duration_ms"),
        "status": "ok",
        "redacted": sum(1 for s in strings if contains_sensitive(s)),
    }
    problems: list[str] = []
    if any(contains_injection(s) for s in strings):
        problems.append("tool response contains instruction-like content (treated as untrusted data)")
    parsed = parse_mcp_tool(name)
    if parsed and parsed[0] in (cfg := mcp_config())["servers"] and parsed[1] in cfg["servers"][parsed[0]]["ops"]:
        schema = cfg["servers"][parsed[0]]["ops"][parsed[1]]["response"]
        if errs := list(Draft202012Validator(schema).iter_errors(resp)):
            problems.append(f"response schema violation: {errs[0].message[:100]}")
    if problems:
        fields["status"] = "rejected"
        fields["problems"] = problems
    record("evidence", **fields)
    if problems:
        print("BLOCKED: " + "; ".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
