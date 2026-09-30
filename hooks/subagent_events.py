#!/usr/bin/env python3
"""SubagentStart / SubagentStop: record agent/version, task, parent, outcome, token usage and handoff summary hashes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hooks._common import digest, read_input, record  # noqa: E402


def main() -> int:
    data = read_input()
    event = str(data.get("hook_event_name", "SubagentEvent"))
    record(
        "subagents",
        event=event,
        agent_type=data.get("agent_type"),
        agent_id=data.get("agent_id"),
        session_id=data.get("session_id"),
        task_hash=digest(data.get("prompt") or data.get("description")),
        handoff_summary_hash=digest(data.get("last_assistant_message")),
        token_usage=data.get("usage") or data.get("token_usage"),
        outcome="stopped" if event == "SubagentStop" else "started",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
