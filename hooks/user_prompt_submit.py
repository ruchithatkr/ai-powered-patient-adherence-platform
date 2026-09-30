#!/usr/bin/env python3
"""UserPromptSubmit: detect secrets/PHI, reject prompt-injection patterns, attach request/trace IDs. Fails closed."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardrails.checks import check_input  # noqa: E402
from hooks._common import digest, read_input, record  # noqa: E402


def main() -> int:
    prompt = str(read_input().get("prompt", ""))
    bad = [g.guardrail for g in check_input(prompt) if g.decision == "BLOCK"]
    rid, tid = "rq-" + uuid.uuid4().hex[:8], "tr-" + uuid.uuid4().hex[:12]
    record("user_prompt", request_id=rid, trace_id=tid, prompt_hash=digest(prompt), blocked=bad)
    if bad:
        print("BLOCKED by guardrail: " + ", ".join(bad), file=sys.stderr)
        return 2
    print(f"request_id={rid} trace_id={tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
