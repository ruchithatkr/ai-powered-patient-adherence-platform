#!/usr/bin/env python3
"""PostToolUseFailure: prevent blind retry of non-idempotent actions and raise an operational alert."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hooks._common import digest, mcp_config, parse_mcp_tool, read_input, record  # noqa: E402

WRITE_WORDS = ("send", "delete", "write", "create", "post", "update", "append", "submit")


def main() -> int:
    data = read_input()
    name = str(data.get("tool_name", ""))
    non_idempotent = any(w in name.lower() for w in WRITE_WORDS)
    if (parsed := parse_mcp_tool(name)) and parsed[0] in (cfg := mcp_config())["servers"]:
        spec = cfg["servers"][parsed[0]]["ops"].get(parsed[1])
        non_idempotent = bool(spec and spec.get("write")) or non_idempotent
    record("tool_failures", tool=name, non_idempotent=non_idempotent, error_hash=digest(data.get("error")))
    if non_idempotent:
        record("alerts", rule="NON_IDEMPOTENT_TOOL_FAILURE", tool=name, severity="high")
        print(
            f"ALERT: {name} failed and may have side effects. Do NOT retry automatically; verify state and ask a human.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
