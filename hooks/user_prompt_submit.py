#!/usr/bin/env python3
"""UserPromptSubmit hook: block secrets/PHI/injection patterns; attach trace id. Fails closed."""
import json, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardrails.checks import check_input
try:
    prompt = json.load(sys.stdin).get("prompt", "")
except Exception:
    print("BLOCKED: unreadable hook input", file=sys.stderr); sys.exit(2)
bad = [g for g in check_input(prompt) if g.decision == "BLOCK"]
if bad:
    print("BLOCKED by guardrail: " + ", ".join(g.guardrail for g in bad), file=sys.stderr); sys.exit(2)
print(f"trace_id=tr-{uuid.uuid4().hex[:12]}")
