#!/usr/bin/env python3
"""SessionStart: verify environment, approved model list, policy version and synthetic-data mode. Fails closed (exit 2)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hooks._common import ROOT, record  # noqa: E402


def main() -> int:
    errs: list[str] = []
    for rel in (
        "mcp/servers.yaml",
        "evals/thresholds.yaml",
        "workflows/policy.yaml",
        "workflows/templates.yaml",
        "guardrails/patterns.yaml",
        "governance/approved_models.yaml",
    ):
        if not (ROOT / rel).exists():
            errs.append(f"missing {rel}")
    for var in ("ALLOW_REAL_PHI", "ADHERENCE_ALLOW_REAL_PHI"):
        if os.environ.get(var):
            errs.append(f"{var} is set: real PHI mode is forbidden locally")
    policy_version = "unknown"
    if not errs:
        import yaml

        from workflows.policy import Policy

        pol = Policy.load(ROOT / "workflows" / "policy.yaml")
        policy_version = f"{pol.policy_version} ({pol.digest})"
        approved = yaml.safe_load((ROOT / "governance" / "approved_models.yaml").read_text())
        from mcp.adapters import RiskModelAdapter  # noqa: F401  (module must import cleanly)

        if "adherence-risk-deterministic-fallback@0.1.0" not in approved["risk_models"]:
            errs.append("running risk model is not on the approved list")
    if errs:
        print("BLOCKED: " + "; ".join(errs), file=sys.stderr)
        return 2
    record("session_start", policy=policy_version, synthetic_mode=True)
    print(f"Session OK: synthetic-data mode, {policy_version}, approved models verified, MCP allowlist present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
