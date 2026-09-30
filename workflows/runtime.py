"""Runtime container: every collaborator is injected here (no module-level singletons)."""

import asyncio
from dataclasses import dataclass, field

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
from domain.clock import Clock
from mcp.adapters import Backend
from mcp.gateway import ApprovalRegistry, Gateway
from memory.store import LongTermMemory, WorkflowStoreProtocol
from observability.alerts import AlertEngine
from observability.audit import AuditStore
from observability.metrics import Metrics
from observability.tracing import Tracer
from workflows.policy import Policy
from workflows.templates import TemplateLibrary


@dataclass
class Runtime:
    settings: Settings
    policy: Policy
    templates: TemplateLibrary
    clock: Clock
    audit: AuditStore
    tracer: Tracer
    metrics: Metrics
    alerts: AlertEngine
    backend: Backend
    gateway: Gateway
    approvals: ApprovalRegistry
    store: WorkflowStoreProtocol
    memory: LongTermMemory
    consent_agent: ConsentAccessAgent
    context_agent: PatientContextAgent
    risk_agent: AdherenceRiskAgent
    planner: InterventionPlannerAgent
    safety: ContentSafetyAgent
    reviewer: HumanReviewAgent
    delivery: DeliveryAgent
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
