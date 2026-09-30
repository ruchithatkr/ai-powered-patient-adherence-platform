from datetime import UTC, datetime
from typing import Any

import pytest

from agents.supervisor import Supervisor
from app.container import build_runtime
from app.settings import Settings
from domain.clock import FixedClock
from workflows.runtime import Runtime

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)  # fixed instant: deterministic quiet-hour behaviour


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def rt(clock: FixedClock) -> Runtime:
    return build_runtime(Settings(audit_path=None), clock=clock)


@pytest.fixture
def sup(rt: Runtime) -> Supervisor:
    return Supervisor(rt)


async def run(
    sup: Supervisor, ref: str, msg: str = "", role: str = "care_coordinator", ident: str | None = None, **kw: Any
) -> Any:
    return await sup.start(
        patient_ref=ref, purpose="adherence_support", requester="coord-1", requester_role=role,
        identity_patient_ref=ident or ref, patient_message=msg, **kw,
    )  # fmt: skip


async def approve(sup: Supervisor, st: Any, decision: str = "APPROVE") -> Any:
    return await sup.decide(
        st.trace_id, approver="rph-1", role="pharmacist", decision=decision, rationale="reviewed evidence packet"
    )
