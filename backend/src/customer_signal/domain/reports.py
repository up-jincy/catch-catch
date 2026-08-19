"""Contracts shared by deterministic analytics, agents, APIs, and the UI."""

from typing import Literal

from pydantic import AwareDatetime, Field

from customer_signal.domain.models import DomainModel, EventType, SourceId


class AnalysisScope(DomainModel):
    """The explicit source and time boundary used for an analysis."""

    start_at: AwareDatetime
    end_at: AwareDatetime
    enabled_sources: list[SourceId]
    population_description: str


class Metric(DomainModel):
    """A deterministic aggregate linked to its tool result."""

    label: str
    value: float | int | str
    unit: str | None = None
    result_id: str


class Finding(DomainModel):
    """An evidence-backed analytical finding."""

    title: str
    description: str
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(default_factory=list)


class Recommendation(DomainModel):
    """A next action justified by evidence from the current run."""

    action_id: Literal[
        "care_call",
        "network_diagnosis",
        "content_improvement",
        "funnel_improvement",
        "campaign_target",
        "further_analysis",
    ]
    title: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class Signal(DomainModel):
    """One scored, evidence-backed customer risk signal."""

    code: str
    label: str
    score: float
    evidence_ids: list[str] = Field(default_factory=list)


class SignalContribution(DomainModel):
    """Signals and score contributed by one source."""

    source_id: SourceId
    score: float
    signals: list[Signal] = Field(default_factory=list)


class RankedCustomer(DomainModel):
    """A scored customer projection for ranked result views."""

    customer_id: str
    risk_score: float
    risk_level: Literal["high", "medium", "low"]
    signals: list[Signal] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    last_event_at: AwareDatetime | None = None


class JourneyEvent(DomainModel):
    """A display-safe event projection for a customer timeline."""

    event_id: str
    evidence_id: str
    source_id: SourceId
    occurred_at: AwareDatetime
    event_type: EventType
    action: str
    topic: str
    outcome: str
    text: str


class InsightReport(DomainModel):
    """The structured, evidence-backed result returned for one analysis."""

    analysis_type: Literal["cohort", "journey", "funnel", "pain_point", "general"]
    scope: AnalysisScope
    headline: str
    executive_summary: str
    metrics: list[Metric] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    signal_contributions: list[SignalContribution] = Field(default_factory=list)
    ranked_customers: list[RankedCustomer] = Field(default_factory=list)
    representative_journeys: list[JourneyEvent] = Field(default_factory=list)
    representative_journey_ids: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    sources_used: list[SourceId] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
