#!/usr/bin/env python3
"""PreToolUse hook: deny tools that call MCP servers outside the allowlist. Fails closed."""
import json, sys
from pathlib import Path
import yaml
cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "mcp" / "servers.yaml").read_text())
try:
    name = json.load(sys.stdin).get("tool_name", "")
except Exception:
    print("BLOCKED: unreadable hook input", file=sys.stderr); sys.exit(2)
if name.startswith("mcp__"):
    server = name.split("__")[1]
    if server not in cfg["servers"]:
        print(f"BLOCKED: MCP server '{server}' not allowlisted", file=sys.stderr); sys.exit(2)
sys.exit(0)
