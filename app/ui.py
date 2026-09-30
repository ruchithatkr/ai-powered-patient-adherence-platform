"""Streamlit prototype UI (synthetic data only). Talks to the FastAPI service."""

import os
from typing import Any

import httpx
import streamlit as st

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
st.set_page_config(page_title="Patient Adherence Platform", page_icon="💊", layout="wide")
st.title("💊 AI-Powered Patient Adherence Platform")
st.warning(
    "Prototype · synthetic data only · supports adherence outreach; it does not diagnose, prescribe or change doses. "
    "Evaluation results: NOT YET MEASURED."
)


def call(method: str, path: str, role: str, actor: str, **kw: Any) -> tuple[int, Any]:
    r = httpx.request(method, API + path, headers={"X-Actor": actor, "X-Role": role}, timeout=15, **kw)
    return r.status_code, r.json()


tab1, tab2, tab3, tab4 = st.tabs(
    [
        "1 · Run workflow (care coordinator)",
        "2 · Human review (pharmacist / nurse / clinician)",
        "3 · Trace & audit",
        "4 · Operations",
    ]
)

with tab1:
    c1, c2 = st.columns(2)
    ref = c1.selectbox("Synthetic patient", [f"P-00{i}" for i in range(1, 8)])
    ident = c1.text_input("Identity context patient ref (must match)", value=ref)
    msg = c2.text_area("Optional patient message (free text is screened, never logged)", "")
    if st.button("Run adherence workflow", type="primary"):
        code, d = call(
            "POST",
            "/workflows",
            "care_coordinator",
            "coordinator-1",
            json={"patient_ref": ref, "identity_patient_ref": ident, "patient_message": msg},
        )
        st.session_state["last"] = d
    d = st.session_state.get("last")
    if d:
        st.subheader(f"Status: {d['status']}")
        st.caption(f"trace_id `{d['trace_id']}` · stage `{d['stage']}` · trace complete: {d['trace_complete']}")
        if d.get("stop_reason"):
            st.error(f"Stopped: {d['stop_reason']}")
        st.info(d["rationale"] or "—")
        r = d.get("risk_assessment")
        if r:
            a, b, c = st.columns(3)
            a.metric("Risk band", r["band"])
            b.metric("Score (uncalibrated)", r["score"])
            c.metric("Confidence", r["confidence"])
            st.write("**Factors**", [f["detail"] for f in r["factors"]])
            st.caption("Limitations: " + " · ".join(r["limitations"]))
        p = d.get("intervention_plan")
        if p:
            st.write(
                f"**Proposed message** ({p['template_id']} v{p['template_version']} via {p['channel']}); "
                f"approval reasons: {p['approval_reasons'] or 'standing approval'}"
            )
            st.code(p["body"], language=None)
            if p.get("citation"):
                st.caption("Source: " + p["citation"])
        if d.get("action_receipt"):
            st.success(f"Receipt {d['action_receipt']['receipt_id']} · {d['action_receipt']['delivery_status']} (simulated)")
        if d.get("review_task_id"):
            st.write("Review task:", d["review_task_id"])

with tab2:
    tid = st.text_input("Trace ID awaiting approval", value=(st.session_state.get("last") or {}).get("trace_id", ""))
    role = st.selectbox("Reviewer role", ["pharmacist", "nurse", "clinician"])
    who = st.text_input("Reviewer identity", "reviewer-1")
    why = st.text_input("Rationale (required)")
    cond = st.text_input("Conditions (optional)")
    a, b = st.columns(2)
    for col, dec in ((a, "APPROVE"), (b, "REJECT")):
        if col.button(dec):
            code, out = call(
                "POST", f"/workflows/{tid}/decision", role, who, json={"decision": dec, "rationale": why, "conditions": cond}
            )
            (st.success if code == 200 else st.error)(f"{code}: " + (out.get("status", "") if code == 200 else str(out)))
            if code == 200:
                st.session_state["last"] = out
    code, tasks = call("GET", "/review-tasks", role, who)
    if code == 200:
        st.write("Open review tasks", tasks)

with tab3:
    tid2 = st.text_input("Trace ID", value=(st.session_state.get("last") or {}).get("trace_id", ""), key="t2")
    if st.button("Load trace") and tid2:
        _, tr = call("GET", f"/workflows/{tid2}/trace", "privacy_officer", "auditor")
        st.dataframe(
            [{k: e.get(k) for k in ("timestamp", "actor", "node", "status", "tool", "error_code", "approval_id")} for e in tr]
        )
        _, ex = call("GET", f"/workflows/{tid2}/explanation", "privacy_officer", "auditor")
        st.write("**Patient-readable explanation**", ex)
        _, wf = call("GET", f"/workflows/{tid2}", "privacy_officer", "auditor")
        st.write("**Traceability chain coverage**", wf.get("chain_links"))
    st.write("Audit chain:", call("GET", "/audit/verify", "privacy_officer", "auditor")[1])

with tab4:
    code, ops = call("GET", "/ops/summary", "privacy_officer", "ops-viewer")
    if code != 200:
        st.error(f"Could not load operations summary ({code})")
    else:
        a, b, c, d = st.columns(4)
        a.metric("Success rate", ops["success_rate"] if ops["success_rate"] is not None else "n/a")
        b.metric("Tool error rate", ops["tool_error_rate"] if ops["tool_error_rate"] is not None else "n/a")
        c.metric("Guardrail block rate", ops["block_rate"] if ops["block_rate"] is not None else "n/a")
        d.metric("Abstain rate", ops["abstain_rate"] if ops["abstain_rate"] is not None else "n/a")
        e, f, g = st.columns(3)
        e.metric("Human override rate", ops["override_rate"] if ops["override_rate"] is not None else "n/a")
        f.metric("Audit events", ops["audit"]["events"])
        g.metric("Audit chain valid", "yes" if ops["audit"]["chain_valid"] else "NO")
        st.write("**Workflows by status**", ops["workflows"])
        st.write("**Tool latency (ms)**", ops["latency_ms"])
        st.write("**Tokens / cost**", ops["tokens_and_cost"])
        st.write("**Drift**", ops["drift_psi"] if ops["drift_psi"] is not None else ops["drift_note"])
        st.write("**Trace gaps**", ops["trace_gaps"] or "none")
        st.subheader("Alerts")
        st.dataframe(ops["alerts"] or [{"rule": "none"}])
