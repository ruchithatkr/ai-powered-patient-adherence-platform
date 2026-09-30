"""FastAPI dependencies.

Identity is a PLACEHOLDER: headers are trusted for local synthetic-data use only. Production must replace
`get_principal` with verified OIDC/SSO tokens + MFA (out of scope for this build; see docs/architecture.md).
"""

import re

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel

from agents.supervisor import Supervisor
from workflows.runtime import Runtime

ROLES = {"care_coordinator", "pharmacist", "nurse", "clinician", "privacy_officer"}
REVIEWERS = {"pharmacist", "nurse", "clinician"}
_ACTOR = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


class Principal(BaseModel):
    actor: str
    role: str


def get_principal(x_actor: str | None = Header(None), x_role: str | None = Header(None)) -> Principal:
    if not x_actor or not x_role:
        raise HTTPException(401, "identity headers required (placeholder auth)")
    if not _ACTOR.match(x_actor) or x_role not in ROLES:
        raise HTTPException(403, "unknown actor or role")
    return Principal(actor=x_actor, role=x_role)


def get_runtime(request: Request) -> Runtime:
    rt: Runtime = request.app.state.runtime
    return rt


def get_supervisor(request: Request) -> Supervisor:
    sup: Supervisor = request.app.state.supervisor
    return sup
