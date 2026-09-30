"""Composition root: builds the Runtime with all dependencies injected."""

from pathlib import Path

import yaml

from agents.subagents import (
    AdherenceRiskAgent,
    ConsentAccessAgent,
    ContentSafetyAgent,
    DeliveryAgent,
    HumanReviewAgent,
    InterventionPlannerAgent,
    PatientContextAgent,
)
from app.settings import Settings
from domain.clock import Clock, SystemClock
from domain.models import Consent
from mcp.adapters import Backend, build_adapters
from mcp.gateway import ApprovalRegistry, Gateway, load_config
from memory.store import LongTermMemory, WorkflowStore
from observability.alerts import AlertEngine
from observability.audit import AuditStore
from observability.metrics import Metrics
from observability.tracing import Tracer, setup_tracing
from workflows.policy import Policy
from workflows.runtime import Runtime
from workflows.templates import TemplateLibrary


def build_runtime(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    audit_path: Path | None | str = "default",
) -> Runtime:
    settings = settings or Settings()
    clock = clock or SystemClock()
    setup_tracing(settings.otel_console)
    policy = Policy.load(settings.policy_path)
    templates = TemplateLibrary.load(settings.templates_path)
    audit = AuditStore(settings.audit_path if audit_path == "default" else audit_path)  # type: ignore[arg-type]
    metrics = Metrics()
    tracer = Tracer(audit, clock)
    backend = Backend(clock, settings.kb_path, policy.operations.notification_rate_limit_per_minute)
    approvals = ApprovalRegistry(clock)

    async def consent_lookup(patient_ref: str) -> Consent | None:
        return backend.get_consent(patient_ref)

    gateway = Gateway(
        config=load_config(settings.mcp_config_path), adapters=build_adapters(backend, audit), tracer=tracer,
        approvals=approvals, consent_lookup=consent_lookup, clock=clock, metrics=metrics,
        timeout_s=policy.operations.tool_timeout_seconds,
    )  # fmt: skip
    memory = LongTermMemory(clock, retention_days=policy.operations.memory_retention_days)
    baseline = None
    if settings.drift_baseline_path and settings.drift_baseline_path.exists():
        baseline = yaml.safe_load(settings.drift_baseline_path.read_text())
    return Runtime(
        settings=settings, policy=policy, templates=templates, clock=clock, audit=audit, tracer=tracer, metrics=metrics,
        alerts=AlertEngine(audit, policy, clock, baseline), backend=backend, gateway=gateway, approvals=approvals,
        store=WorkflowStore(clock, policy.operations.workflow_ttl_hours), memory=memory,
        consent_agent=ConsentAccessAgent(tracer, gateway, clock),
        context_agent=PatientContextAgent(tracer, gateway, memory, clock, policy),
        risk_agent=AdherenceRiskAgent(tracer, gateway, clock, policy),
        planner=InterventionPlannerAgent(tracer, gateway, clock, policy, templates),
        safety=ContentSafetyAgent(tracer, gateway, clock, policy, templates),
        reviewer=HumanReviewAgent(tracer, gateway, policy),
        delivery=DeliveryAgent(tracer, gateway, clock),
    )  # fmt: skip
