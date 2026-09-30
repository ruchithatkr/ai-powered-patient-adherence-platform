"""Explicit state machine: intake -> authorize -> retrieve -> assess -> plan -> safety_check -> approval -> execute -> verify -> close."""
STAGES = ["intake", "authorize", "retrieve", "assess", "plan", "safety_check", "approval", "execute", "verify", "close"]
APPROVER_ROLES = {"pharmacist", "nurse", "clinician"}
REQUESTER_ROLES = {"care_coordinator"}
REQUIRED_TRACE_NODES = ["request", "agent_plan", "stage:authorize", "stage:retrieve", "stage:assess", "final"]
POLICY_VERSION = "POLICY-1.0"
WORKFLOW_VERSION = "wf-1.0"
STALE_DAYS = 30


def next_stage(stage: str) -> str:
    return STAGES[STAGES.index(stage) + 1]


def required_nodes(st) -> list[str]:
    """Trace nodes that must exist for a workflow in its current state."""
    base = ["request", "agent_plan"]
    if st.status.value == "STOPPED" or st.status.value == "ESCALATED" and st.risk_assessment is None:
        base += ["stop"]
    else:
        base += ["stage:authorize", "stage:retrieve", "stage:assess"]
    if st.status.value == "AWAITING_APPROVAL":
        return base + ["approval_requested"]
    if st.approval:
        base.append("approval")
    return base + ["final"]
