"""Shared helpers for Claude Code hooks. Hooks are versioned, deterministic and fail closed (exit 2) for safety controls."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "var" / "hooks"


def read_input() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except Exception:  # fail closed: unreadable hook input is a control failure
        print("BLOCKED: unreadable hook input", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("BLOCKED: hook input must be a JSON object", file=sys.stderr)
        sys.exit(2)
    return data


def record(name: str, **fields: Any) -> None:
    """Append a PHI-safe hook record (hashes/counters only)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / f"{name}.jsonl").open("a") as f:
        f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **fields}, default=str) + "\n")


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def parse_mcp_tool(name: str) -> tuple[str, str] | None:
    """mcp__<server>__<op> -> (server, op)."""
    parts = name.split("__")
    return (parts[1], "__".join(parts[2:])) if name.startswith("mcp__") and len(parts) >= 3 else None


def mcp_config() -> dict[str, Any]:
    import yaml

    cfg: dict[str, Any] = yaml.safe_load((ROOT / "mcp" / "servers.yaml").read_text())
    return cfg
