#!/usr/bin/env python3
"""PreToolUse: enforce MCP allowlist, scopes, parameter schema, purpose and human-approval token. Fails closed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jsonschema import Draft202012Validator  # noqa: E402

from guardrails.checks import contains_sensitive  # noqa: E402
from hooks._common import mcp_config, parse_mcp_tool, read_input, record  # noqa: E402


def main() -> int:
    data = read_input()
    name = str(data.get("tool_name", ""))
    parsed = parse_mcp_tool(name)
    if parsed is None:
        return 0  # not an MCP tool; other permission layers apply
    server, op = parsed
    cfg = mcp_config()
    srv = cfg["servers"].get(server)
    if srv is None:
        return _deny(name, f"MCP server '{server}' not allowlisted")
    spec = srv["ops"].get(op)
    if spec is None:
        return _deny(name, f"operation '{op}' not permitted on '{server}'")
    params = data.get("tool_input") or {}
    purpose = params.get("purpose", "adherence_support")
    if purpose not in cfg["purposes"]:
        return _deny(name, f"purpose '{purpose}' not allowed")
    payload = {k: v for k, v in params.items() if k not in ("purpose", "approval_token")}
    if errs := list(Draft202012Validator(spec["params"]).iter_errors(payload)):
        return _deny(name, f"parameter schema violation: {errs[0].message[:100]}")
    if any(isinstance(v, str) and contains_sensitive(v) for v in payload.values()):
        return _deny(name, "parameters contain secret/PHI-like content")
    if spec.get("approval") and not params.get("approval_token"):
        return _deny(name, "human-approval token required for external write")
    record("pre_tool_use", tool=name, decision="allow")
    return 0


def _deny(tool: str, why: str) -> int:
    record("pre_tool_use", tool=tool, decision="deny", reason=why)
    print(f"BLOCKED: {why}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
