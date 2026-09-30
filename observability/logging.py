"""Structured JSON logging with PHI/secret scrubbing. Never logs raw prompts, patient text, tokens or identifiers."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from guardrails.checks import contains_sensitive

SAFE_EXTRA = {
    "trace_id", "request_id", "method", "path", "status_code", "latency_ms", "event", "agent", "tool", "error_code",
}  # fmt: skip


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if kind := contains_sensitive(msg):
            msg = f"[REDACTED:{kind}]"
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        for k in SAFE_EXTRA:
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("adherence")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False
