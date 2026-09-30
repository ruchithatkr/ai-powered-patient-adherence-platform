#!/usr/bin/env python3
"""PreCompact: persist only approved state summaries; exclude raw PHI and secrets.

Compaction summaries are model-generated, so this hook (1) instructs the summariser what to keep/exclude and
(2) counts secret/PHI-like lines in the transcript (counts only) and records them for follow-up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardrails.checks import contains_sensitive  # noqa: E402
from hooks._common import read_input, record  # noqa: E402

INSTRUCTIONS = (
    "Summarise workflow STATE only: trace/request IDs, decisions and their rationale, approvals, test/lint/eval commands and "
    "actual results, open risks, next steps. EXCLUDE raw patient text, direct identifiers, PHI, secrets, tokens and raw prompts. "
    "Never state that a test, approval, metric or deployment happened unless evidence exists in this session."
)


def main() -> int:
    data = read_input()
    flagged = 0
    if (tp := data.get("transcript_path")) and Path(tp).is_file():
        for line in Path(tp).read_text(errors="ignore").splitlines():
            if contains_sensitive(line[:4000]):
                flagged += 1
    record("pre_compact", trigger=data.get("trigger"), sensitive_like_lines=flagged)
    if flagged:
        print(f"[pre-compact] {flagged} transcript lines looked secret/PHI-like; exclude them from the summary.", file=sys.stderr)
    print(INSTRUCTIONS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
