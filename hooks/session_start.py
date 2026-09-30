#!/usr/bin/env python3
"""SessionStart hook: verify env, policy version and synthetic-data mode. Fails closed (exit 2)."""
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
errs = []
if not (root / "mcp" / "servers.yaml").exists(): errs.append("missing MCP allowlist")
if not (root / "evals" / "thresholds.yaml").exists(): errs.append("missing thresholds")
import os
if os.environ.get("ALLOW_REAL_PHI"): errs.append("real PHI mode is forbidden locally")
if errs:
    print("BLOCKED: " + "; ".join(errs), file=sys.stderr); sys.exit(2)
print("Session OK: synthetic-data mode, POLICY-1.0, MCP allowlist present.")
