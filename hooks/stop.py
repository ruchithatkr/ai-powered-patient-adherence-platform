#!/usr/bin/env python3
"""Stop: run tests/lint/types, confirm audit completeness on synthetic workflows, print unresolved risks."""

import asyncio
import subprocess  # nosec B404 - fixed local tool invocations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hooks._common import ROOT, read_input, record  # noqa: E402


def run(*cmd: str) -> tuple[bool, str]:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)  # noqa: S603  # nosec B603 - fixed argv, no untrusted input
    tail = (r.stdout.strip().splitlines() or r.stderr.strip().splitlines() or ["(no output)"])[-1]
    return r.returncode == 0, tail


async def audit_complete() -> bool:
    from agents.supervisor import Supervisor
    from app.container import build_runtime
    from app.settings import Settings

    rt = build_runtime(Settings(audit_path=None))
    sup = Supervisor(rt)
    for ref in (f"P-00{i}" for i in range(1, 8)):
        st = await sup.start(
            patient_ref=ref,
            purpose="adherence_support",
            requester="stop-hook",
            requester_role="care_coordinator",
            identity_patient_ref=ref,
        )
        if rt.audit.gaps(st.trace_id):
            return False
    return rt.audit.verify_chain()


def main() -> int:
    data = read_input()
    py = sys.executable
    checks = {
        "pytest": run(py, "-m", "pytest", "-q", "-p", "no:cacheprovider"),
        "ruff": run(py, "-m", "ruff", "check", "."),
        "mypy": run(py, "-m", "mypy", "."),
    }
    audit_ok = asyncio.run(audit_complete())
    for name, (ok, tail) in checks.items():
        print(f"{name}: {'ok' if ok else 'FAILED'} - {tail}")
    print(f"audit completeness (synthetic workflows): {'ok' if audit_ok else 'GAPS FOUND'}")
    risks = ROOT / "docs" / "risk-register.md"
    open_risks = sum(1 for line in risks.read_text().splitlines() if "| OPEN |" in line) if risks.exists() else "unknown"
    print(
        f"Unresolved risks: {open_risks} open in docs/risk-register.md; model metrics remain NOT YET MEASURED; no signed release record."
    )
    failed = [n for n, (ok, _) in checks.items() if not ok] + ([] if audit_ok else ["audit"])
    record("stop", failed=failed)
    if failed and not data.get("stop_hook_active"):
        print("Fix failing checks before finishing: " + ", ".join(failed), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
