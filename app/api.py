"""FastAPI entry point. PROTOTYPE AUTH: identity comes from headers; production must use MFA-backed SSO/OIDC."""
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from agents.supervisor import Supervisor
from memory.store import ConflictError
from workflows.definition import required_nodes

app = FastAPI(title="AI-Powered Patient Adherence Platform", version="1.0.0")
sup = Supervisor()


class Identity(BaseModel):
    actor: str
    role: str


def identity(x_actor: str = Header(...), x_role: str = Header(...)) -> Identity:
    return Identity(actor=x_actor, role=x_role)


class StartReq(BaseModel):
    patient_ref: str
    identity_patient_ref: str
    purpose: str = "adherence_support"
    patient_message: str = ""


class DecisionReq(BaseModel):
    decision: str
    rationale: str
    conditions: str = ""
    expected_version: int | None = None


def _view(st):
    d = st.model_dump(mode="json")
    d["trace_complete"] = not sup.audit.completeness(st.trace_id, required_nodes(st))
    return d


@app.get("/health")
def health():
    return {"status": "ok", "synthetic_data_only": True, "audit_chain_valid": sup.audit.verify_chain()}


@app.get("/patients")
def patients(_: Identity = Depends(identity)):
    return [{"patient_ref": k} for k in sup.backend.db]


@app.post("/workflows")
def start(req: StartReq, who: Identity = Depends(identity)):
    return _view(sup.start(patient_ref=req.patient_ref, purpose=req.purpose, requester=who.actor, requester_role=who.role,
                           identity_patient_ref=req.identity_patient_ref, patient_message=req.patient_message))


@app.get("/workflows/{trace_id}")
def get(trace_id: str, _: Identity = Depends(identity)):
    st = sup.store.get(trace_id)
    if not st:
        raise HTTPException(404, "not found or expired")
    return _view(st)


@app.post("/workflows/{trace_id}/decision")
def decide(trace_id: str, req: DecisionReq, who: Identity = Depends(identity)):
    if req.decision not in ("APPROVE", "REJECT"):
        raise HTTPException(422, "decision must be APPROVE or REJECT")
    try:
        return _view(sup.decide(trace_id, approver=who.actor, role=who.role, decision=req.decision,
                                rationale=req.rationale, conditions=req.conditions, expected_version=req.expected_version))
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (PermissionError, ValueError, ConflictError) as e:
        raise HTTPException(403, str(e))


@app.get("/workflows/{trace_id}/trace")
def trace(trace_id: str, _: Identity = Depends(identity)):
    return sup.audit.export_redacted(trace_id)


@app.get("/workflows/{trace_id}/explanation")
def explanation(trace_id: str, _: Identity = Depends(identity)):
    try:
        return sup.explain(trace_id)
    except KeyError:
        raise HTTPException(404, "not found")


@app.get("/review-tasks")
def review_tasks(who: Identity = Depends(identity)):
    if who.role not in ("pharmacist", "nurse", "clinician"):
        raise HTTPException(403, "reviewers only")
    return list(sup.backend.tasks.values())


@app.post("/patients/{patient_ref}/withdraw-consent")
def withdraw(patient_ref: str, who: Identity = Depends(identity)):
    sup.withdraw_consent(patient_ref, who.actor)
    return {"patient_ref": patient_ref, "consent": "withdrawn"}


@app.delete("/patients/{patient_ref}/data")
def delete(patient_ref: str, who: Identity = Depends(identity)):
    if who.role != "privacy_officer":
        raise HTTPException(403, "privacy_officer only")
    return sup.delete_patient(patient_ref, who.actor)


@app.get("/audit/verify")
def verify(_: Identity = Depends(identity)):
    return {"chain_valid": sup.audit.verify_chain(), "events": len(sup.audit.all())}
