#!/usr/bin/env python3
"""Change-control gate: governed files (MCP permissions, templates, policy, thresholds, patterns, approved models) may only
change together with a recorded approval in governance/change-approvals.yaml.

Exit 1 when a governed file's digest differs from its last recorded approval entry.
Use `--record <file> --approver NAME --role ROLE --reason TEXT` to append an approval (a human must run this).
"""

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "governance" / "change-approvals.yaml"
GOVERNED = [
    "mcp/servers.yaml", "workflows/templates.yaml", "workflows/policy.yaml", "evals/thresholds.yaml",
    "guardrails/patterns.yaml", "governance/approved_models.yaml",
]  # fmt: skip


def digest(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def load() -> dict[str, list[dict[str, str]]]:
    return yaml.safe_load(LEDGER.read_text())["approvals"] if LEDGER.exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record")
    ap.add_argument("--approver")
    ap.add_argument("--role")
    ap.add_argument("--reason")
    a = ap.parse_args()
    ledger = load()
    if a.record:
        if not (a.approver and a.role and a.reason):
            print("--approver, --role and --reason are required", file=sys.stderr)
            return 2
        ledger.setdefault(a.record, []).append(
            {
                "sha256": digest(a.record),
                "approver": a.approver,
                "role": a.role,
                "reason": a.reason,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        LEDGER.write_text(
            "# Append-only change-approval ledger. Entries are added by a human approver.\n"
            + yaml.safe_dump({"approvals": ledger}, sort_keys=True)
        )
        print(f"recorded approval for {a.record}")
        return 0
    bad = [rel for rel in GOVERNED if not ledger.get(rel) or ledger[rel][-1]["sha256"] != digest(rel)]
    unapproved = [rel for rel in GOVERNED if ledger.get(rel) and ledger[rel][-1]["role"] == "developer-baseline"]
    for rel in bad:
        print(f"CHANGE NOT APPROVED: {rel} differs from last approved digest (run --record after human approval)")
    for rel in unapproved:
        print(f"note: {rel} baseline recorded by a developer, NOT a clinical/governance approval")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
