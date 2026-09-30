from fastapi.testclient import TestClient
from app.api import app

c = TestClient(app)
COORD = {"X-Actor": "coord-1", "X-Role": "care_coordinator"}
RPH = {"X-Actor": "rph-1", "X-Role": "pharmacist"}


def test_end_to_end_api():
    assert c.get("/health").json()["synthetic_data_only"]
    r = c.post("/workflows", headers=COORD, json={"patient_ref": "P-003", "identity_patient_ref": "P-003"}).json()
    assert r["status"] == "AWAITING_APPROVAL"
    bad = c.post(f"/workflows/{r['trace_id']}/decision", headers=COORD, json={"decision": "APPROVE", "rationale": "x"})
    assert bad.status_code == 403
    ok = c.post(f"/workflows/{r['trace_id']}/decision", headers=RPH, json={"decision": "APPROVE", "rationale": "reviewed"}).json()
    assert ok["status"] == "COMPLETED" and ok["trace_complete"]
    assert len(c.get(f"/workflows/{r['trace_id']}/trace", headers=COORD).json()) > 5
    assert c.get("/audit/verify", headers=COORD).json()["chain_valid"]


def test_deletion_privacy_officer_only():
    assert c.delete("/patients/P-003/data", headers=COORD).status_code == 403
    assert c.delete("/patients/P-003/data", headers={"X-Actor": "p", "X-Role": "privacy_officer"}).status_code == 200
