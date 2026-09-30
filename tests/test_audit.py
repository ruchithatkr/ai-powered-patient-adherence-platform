import json

import pytest

from observability.audit import AuditIntegrityError, AuditStore, chain_coverage


def test_durable_log_survives_restart_and_keeps_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditStore(p)
    a.append(trace_id="t", actor="x", node="request")
    a.append(trace_id="t", actor="x", node="final")
    b = AuditStore(p)  # simulated restart
    assert len(b) == 2 and b.verify_chain()
    b.append(trace_id="t", actor="x", node="more")
    assert AuditStore(p).verify_chain() and len(AuditStore(p)) == 3


def test_tampered_file_fails_closed_on_startup(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditStore(p)
    a.append(trace_id="t", actor="x", node="request", status="OK")
    a.append(trace_id="t", actor="x", node="final")
    lines = p.read_text().splitlines()
    ev = json.loads(lines[0])
    ev["status"] = "EDITED"
    p.write_text(json.dumps(ev) + "\n" + lines[1] + "\n")
    with pytest.raises(AuditIntegrityError):
        AuditStore(p)


def test_deleted_event_breaks_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditStore(p)
    for n in ("request", "agent_plan", "final"):
        a.append(trace_id="t", actor="x", node=n)
    lines = p.read_text().splitlines()
    p.write_text(lines[0] + "\n" + lines[2] + "\n")
    with pytest.raises(AuditIntegrityError):
        AuditStore(p)


def test_incremental_verification_only_checks_new_events():
    a = AuditStore()
    a.append(trace_id="t", actor="x", node="request")
    assert a.verify_chain(incremental=True)
    a.append(trace_id="t", actor="x", node="agent_plan")
    assert a.verify_chain(incremental=True)


def test_gaps_detects_missing_stage_span():
    a = AuditStore()
    for n in ("request", "agent_plan", "stage:intake"):
        a.append(trace_id="t", actor="x", node=n)
    a.append(trace_id="t", actor="x", node="final", stages_visited=["intake", "authorize", "close"])
    assert a.gaps("t") == ["stage:authorize"]
    assert a.gaps("unknown") == ["request"]


def test_chain_coverage_reports_missing_links():
    cov = chain_coverage([{"node": "request", "status": "OK"}])
    assert cov["User Request"] and not cov["Final Output"] and not cov["Human Approval"]
