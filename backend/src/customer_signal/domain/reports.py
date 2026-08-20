"""Legacy Journey and generic customer-signal report contracts."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, FiniteFloat, model_validator

from customer_signal.domain.analysis import AnalysisGoal, VerifiedClaim
from customer_signal.domain.facts import (
    AnalysisJourneyEvent,
    AnalysisMetricFact,
    AnalysisRankedCustomer,
    AnalysisSignal,
)
from customer_signal.domain.models import DomainModel, EventType, SourceId


type Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class AnalysisScope(DomainModel):
    """The explicit source and time boundary used for an analysis."""

    start_at: AwareDatetime
    end_at: AwareDatetime
    enabled_sources: list[SourceId]
    population_description: str

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        return self


class Metric(DomainModel):
    """A deterministic aggregate linked to its tool result."""

    label: str
    value: FiniteFloat | int | str
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
    score: Score
    evidence_ids: list[str] = Field(default_factory=list)


class SignalContribution(DomainModel):
    """Signals and score contributed by one source."""

    source_id: SourceId
    score: Score
    signals: list[Signal] = Field(default_factory=list)


class RankedCustomer(DomainModel):
    """A scored customer projection for ranked result views."""

    customer_id: str
    risk_score: Score
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

    report_kind: Literal["legacy_journey"] = "legacy_journey"
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


class GenericReportModel(DomainModel):
    """Strict base kept separate from legacy report parsing behavior."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisFinding(GenericReportModel):
    """A server-rendered finding backed by one verified Claim."""

    claim: VerifiedClaim
    statement: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(min_length=1, max_length=16)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class AnalysisRecommendation(GenericReportModel):
    """A server-rendered action bound to verified Claims and Facts."""

    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    claim_ids: list[str] = Field(min_length=1, max_length=32)
    fact_ids: list[str] = Field(min_length=1, max_length=32)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class AnalysisReportProvenance(GenericReportModel):
    """Server-owned execution references used to reproduce a report."""

    fact_ids: list[str] = Field(min_length=1, max_length=64)
    result_ids: list[str] = Field(min_length=1, max_length=64)
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    dataset_versions: list[str] = Field(min_length=1, max_length=32)
    adapter_versions: dict[SourceId, str] = Field(min_length=1, max_length=32)
    manifest_versions: dict[SourceId, str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_exact_source_version_coverage(self) -> Self:
        for values, context in (
            (self.fact_ids, "fact_ids"),
            (self.result_ids, "result_ids"),
            (self.source_ids, "source_ids"),
            (self.dataset_versions, "dataset_versions"),
        ):
            if len(values) != len(set(values)) or any(not value for value in values):
                raise ValueError(f"report provenance {context} must be unique and nonblank")
        expected = set(self.source_ids)
        if set(self.adapter_versions) != expected or set(self.manifest_versions) != expected:
            raise ValueError("report provenance versions must exactly cover sources")
        if any(not value for value in self.adapter_versions.values()) or any(
            not value for value in self.manifest_versions.values()
        ):
            raise ValueError("report provenance versions must be nonblank")
        return self


class CustomerSignalReport(GenericReportModel):
    """Generic final report composed only from validated run-scoped Facts and Claims."""

    report_kind: Literal["customer_signal"] = "customer_signal"
    goal: AnalysisGoal
    headline: str = Field(min_length=1, max_length=300)
    executive_summary: str = Field(min_length=1, max_length=2_000)
    metrics: list[AnalysisMetricFact] = Field(default_factory=list, max_length=128)
    signals: list[AnalysisSignal] = Field(default_factory=list, max_length=128)
    ranked_customers: list[AnalysisRankedCustomer] = Field(default_factory=list, max_length=100)
    representative_journeys: list[AnalysisJourneyEvent] = Field(
        default_factory=list, max_length=100
    )
    findings: list[AnalysisFinding] = Field(default_factory=list, max_length=32)
    recommendations: list[AnalysisRecommendation] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    provenance: AnalysisReportProvenance


type ReportContract = Annotated[
    InsightReport | CustomerSignalReport,
    Field(discriminator="report_kind"),
]

# Separately named aliases make the compatibility boundary explicit without
# changing the concrete ``InsightReport`` class used by legacy runners.
type ReportUnion = ReportContract
type GenericOrLegacyReport = ReportContract


__all__ = [
    "AnalysisFinding",
    "AnalysisRecommendation",
    "AnalysisReportProvenance",
    "AnalysisScope",
    "CustomerSignalReport",
    "Finding",
    "GenericOrLegacyReport",
    "InsightReport",
    "JourneyEvent",
    "Metric",
    "RankedCustomer",
    "Recommendation",
    "ReportContract",
    "ReportUnion",
    "Signal",
    "SignalContribution",
]
