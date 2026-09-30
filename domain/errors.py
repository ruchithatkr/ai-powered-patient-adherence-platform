"""Workflow-level control-flow exceptions."""

from domain.models import Status


class WorkflowStop(Exception):
    """Raised by agents/skills to stop the workflow fail-closed with a user-safe rationale."""

    def __init__(self, reason: str, rationale: str = "", status: Status = Status.STOPPED) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rationale = rationale or f"Workflow stopped: {reason}."
        self.status = status

    @property
    def code(self) -> str:
        return self.reason


class SkillError(WorkflowStop):
    pass


class NoIntervention(Exception):
    """The least intrusive action is to do nothing (optionally handing the case to a human)."""

    def __init__(self, reason: str, needs_human: bool = False) -> None:
        super().__init__(reason)
        self.needs_human = needs_human
