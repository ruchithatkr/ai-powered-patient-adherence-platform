"""Explicit state machine: intake -> authorize -> retrieve -> assess -> plan -> safety_check -> approval -> execute -> verify -> close.

Stage handlers return an Outcome; the engine (agents/supervisor.py) owns transitions, persistence and audit.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from domain.errors import WorkflowStop
from domain.models import PatientContext, Status, WorkflowState

if TYPE_CHECKING:
    from mcp.gateway import CallIdentity
    from observability.tracing import Span
    from workflows.runtime import Runtime

STAGES = ["intake", "authorize", "retrieve", "assess", "plan", "safety_check", "approval", "execute", "verify", "close"]
NEXT = dict(zip(STAGES, STAGES[1:], strict=False))
APPROVER_ROLES = {"pharmacist", "nurse", "clinician"}
REQUESTER_ROLES = {"care_coordinator"}
DENIED_STOP_REASONS = {"ROLE_NOT_AUTHORIZED", "IDENTITY_MISMATCH"}


@dataclass
class Continue:
    """Proceed to the next stage."""


@dataclass
class Stop:
    """Fail closed: stop with a reason (missing consent, stale data, tool failure, safety block, ...)."""

    reason: str
    status: Status = Status.STOPPED
    rationale: str = ""


@dataclass
class Wait:
    """Pause until a human decision (persisted; resumed by Supervisor.decide)."""


@dataclass
class Finish:
    """Normal early completion (e.g. no intervention needed) or handoff to a human."""

    status: Status
    rationale: str = ""


Outcome = Continue | Stop | Wait | Finish


@dataclass
class Scratch:
    """Per-run transient inputs. Raw patient text lives here only and is never persisted or logged."""

    identity_patient_ref: str = ""
    patient_message: str = ""
    context: PatientContext | None = None
    extras: dict[str, object] = field(default_factory=dict)


def require[T](value: T | None, what: str) -> T:
    """Fail closed if workflow state needed by a stage is missing (asserts are stripped under python -O)."""
    if value is None:
        raise WorkflowStop("INVALID_STATE", f"Internal workflow state missing: {what}. No action taken.")
    return value


class StageHandler(Protocol):
    name: str

    async def run(self, rt: "Runtime", st: WorkflowState, ident: "CallIdentity", scratch: Scratch, sp: "Span") -> Outcome: ...
