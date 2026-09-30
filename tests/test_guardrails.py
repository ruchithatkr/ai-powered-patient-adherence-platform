import pytest
from datetime import datetime, timezone
from domain.models import Preferences
from domain.synthetic import TEMPLATES
from guardrails.checks import check_content, check_input, in_quiet_hours
from mcp.gateway import Backend, Gateway, GatewayError
from observability.audit import AuditStore


@pytest.mark.parametrize("body", ["You should double your dose", "Stop taking your medicine", "You probably have diabetes",
                                  "Limited offer: buy now", "You must take it or else", "This will cure you"])
def test_unsafe_content_blocked(body):
    assert check_content(body).decision == "BLOCK"


@pytest.mark.parametrize("tid", list(TEMPLATES))
def test_all_approved_templates_pass_content_safety(tid):
    body = TEMPLATES[tid]["body"].format(first_name="A", medication_label="x", time="08:00", kb_text="Tip.")
    assert check_content(body).decision == "PASS"


def test_quiet_hours_use_patient_timezone():
    p = Preferences(timezone="Asia/Kolkata")
    assert in_quiet_hours(p, datetime(2026, 1, 1, 17, 0, tzinfo=timezone.utc))      # 22:30 local
    assert not in_quiet_hours(p, datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc))   # 11:30 local


def test_emergency_input_is_review():
    assert check_input("severe bleeding")[0].decision == "REVIEW"


def gw():
    return Gateway(AuditStore(), Backend())


def call(g, server, op, params, role="care_coordinator", purpose="adherence_support", **kw):
    return g.call(server, op, params, actor="a", role=role, purpose=purpose, trace_id="t", **kw)


def test_gateway_denies_unknown_server_op_role_purpose_schema():
    g = gw()
    for args, code in [(("shell", "exec", {}), "SERVER_NOT_ALLOWLISTED"),
                       (("patient-records-read", "delete", {}), "OP_NOT_ALLOWED"),
                       (("patient-records-read", "get_context", {"patient_ref": "P-001"}, "privacy_officer"), "ROLE_NOT_AUTHORIZED"),
                       (("patient-records-read", "get_context", {"patient_ref": "P-001"}, "care_coordinator", "marketing"), "PURPOSE_NOT_ALLOWED"),
                       (("patient-records-read", "get_context", {}), "SCHEMA_INVALID")]:
        with pytest.raises(GatewayError) as e:
            call(g, *args)
        assert e.value.code == code


def test_external_write_requires_valid_approval_token():
    g = gw()
    p = {"idempotency_key": "k", "channel": "sms", "template_id": "REMINDER_SIMPLE", "body": "hi", "patient_ref": "P-001"}
    for tok in (None, "forged"):
        with pytest.raises(GatewayError) as e:
            call(g, "notification-send", "send", p, approval_token=tok)
        assert e.value.code == "APPROVAL_REQUIRED"
    assert not g.backend.sent


def test_audit_refuses_phi_fields_and_detects_tampering():
    a = AuditStore()
    with pytest.raises(ValueError):
        a.append(trace_id="t", actor="x", node="n", patient_text="secret")
    a.append(trace_id="t", actor="x", node="n")
    a.append(trace_id="t", actor="x", node="m")
    assert a.verify_chain()
    a._events[0]["status"] = "TAMPERED"
    assert not a.verify_chain()
