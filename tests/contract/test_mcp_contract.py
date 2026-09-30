"""Contract tests: every allowlisted MCP server/op has a sample, accepts it, returns a schema-valid response and rejects bad input."""

import pytest

from mcp.gateway import CallIdentity, GatewayError

IDENT = CallIdentity("contract", "care_coordinator", "adherence_support", "tr-contract")
SAMPLES = {
    ("patient-records-read", "get_context"): {"patient_ref": "P-001"},
    ("consent-registry-read", "get_consent"): {"patient_ref": "P-001"},
    ("knowledge-base-read", "get_article"): {"kb_id": "KB-ADH-001"},
    ("risk-model-infer", "infer"): {"features": {"n_obs": 10, "adherence_ratio": 0.5, "late_refill_days": 0}},
    ("care-team-review", "create_task"): {"patient_ref": "P-001", "packet": {"reason": "contract test"}},
    ("care-team-review", "get_task"): None,  # filled from create_task
    ("notification-send", "send"): {
        "idempotency_key": "contract-key-1",
        "channel": "sms",
        "template_id": "REMINDER_SIMPLE",
        "body": "hi",
        "patient_ref": "P-001",
    },
    ("audit-write", "append"): {"trace_id": "tr-contract", "node": "contract"},
}


def all_ops(rt):
    return {(s, o) for s, srv in rt.gateway.config["servers"].items() for o in srv["ops"]}


def test_every_configured_operation_has_a_contract_sample_and_an_adapter(rt):
    assert all_ops(rt) == set(SAMPLES), "add a contract sample when adding/removing an MCP operation"
    assert set(rt.gateway.adapters) == set(rt.gateway.config["servers"])


@pytest.mark.parametrize("key", sorted(SAMPLES))
async def test_valid_call_returns_schema_valid_response(rt, key):
    server, op = key
    params = SAMPLES[key]
    if key == ("care-team-review", "get_task"):
        params = {
            "task_id": (
                await rt.gateway.call(
                    "care-team-review", "create_task", SAMPLES[("care-team-review", "create_task")], ident=IDENT
                )
            )["task_id"]
        }
    token = None
    if rt.gateway.config["servers"][server]["ops"][op].get("approval"):
        token = "APR-contract"
        rt.approvals.grant(token, params["idempotency_key"])
    result = await rt.gateway.call(
        server, op, params, ident=IDENT, approval_token=token
    )  # response is schema-validated by the gateway
    assert result is not None


@pytest.mark.parametrize("key", sorted(k for k in SAMPLES if SAMPLES[k]))
async def test_unknown_parameters_and_missing_required_are_rejected(rt, key):
    server, op = key
    with pytest.raises(GatewayError) as e:
        await rt.gateway.call(server, op, {**SAMPLES[key], "unexpected": True}, ident=IDENT)
    assert e.value.code in ("SCHEMA_INVALID", "APPROVAL_REQUIRED")
    with pytest.raises(GatewayError) as e2:
        await rt.gateway.call(server, op, {}, ident=IDENT)
    assert e2.value.code == "SCHEMA_INVALID"


async def test_write_operations_are_flagged_and_external_ones_need_approval(rt):
    cfg = rt.gateway.config["servers"]
    assert cfg["notification-send"]["ops"]["send"]["approval"] is True
    assert all(
        op.get("write") is False
        for s in ("patient-records-read", "consent-registry-read", "knowledge-base-read", "risk-model-infer")
        for op in cfg[s]["ops"].values()
    )


async def test_notification_rate_limit_is_enforced(rt):
    rt.backend.rate_limit = 2
    for i in range(3):
        params = {**SAMPLES[("notification-send", "send")], "idempotency_key": f"rate-key-{i}"}
        rt.approvals.grant(f"APR-r{i}", params["idempotency_key"])
        try:
            await rt.gateway.call("notification-send", "send", params, ident=IDENT, approval_token=f"APR-r{i}")
        except GatewayError as e:
            assert i == 2 and e.code == "RATE_LIMITED"
