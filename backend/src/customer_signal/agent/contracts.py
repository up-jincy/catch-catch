"""Strict contracts shared by analysis runners and the runtime coordinator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    field_validator,
    model_validator,
)

from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNote,
    AnalysisPlan,
    AnalysisStep,
    ClarificationRequired,
    PublicRunError,
    RunStatus,
    UnsupportedAnalysis,
)
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.reports import (
    CustomerSignalReport,
    InsightReport,
    JourneyEvent,
    RankedCustomer,
)
from customer_signal.domain.types import PrimitiveName, SourceId
from customer_signal.runtime.events import RunnerEvent


type MetricFactValue = FiniteFloat | int | str
type ToolName = PrimitiveName
type EventEmitter = Callable[[RunnerEvent], Awaitable[None] | None]
type ReportValidator = Callable[[InsightReport, "RunFacts"], InsightReport | None]
type AnalysisEventType = Literal[
    "goal_created",
    "clarification_required",
    "unsupported_analysis",
    "plan_created",
    "plan_revised",
    "step_started",
    "fact_created",
    "analysis_note_created",
    "step_completed",
    "report_validating",
    "result",
    "error",
]


class RunnerContract(BaseModel):
    """Forbid accidental fields while retaining JSON datetime parsing at the API edge."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RunRequest(RunnerContract):
    """One bounded customer-signal analysis request."""

    question: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    enabled_sources: list[SourceId] = Field(min_length=1, max_length=32)

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def parse_iso_datetime(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must be a nonblank string")
        return normalized

    @field_validator("enabled_sources")
    @classmethod
    def validate_unique_sources(cls, value: list[SourceId]) -> list[SourceId]:
        if len(value) != len(set(value)):
            raise ValueError("enabled_sources must be unique")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        return self


class AnalysisEvent(RunnerContract):
    """Public generic-analysis event containing no model transcript or hidden reasoning."""

    type: AnalysisEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_private_payload_keys(self) -> Self:
        _assert_public_analysis_payload(self.payload)
        return self


type AnalysisEventEmitter = Callable[[AnalysisEvent], Awaitable[None] | None]


class MetricFact(RunnerContract):
    """One exact, displayable metric authorized by a Tool result."""

    label: str
    value: MetricFactValue
    unit: str | None = None


class RunFacts(RunnerContract):
    """Claims authorized by structured tool results from one run."""

    tool_result_ids: dict[ToolName, str]
    allowed_customer_ids: frozenset[str]
    allowed_evidence_ids: frozenset[str]
    fetched_evidence_ids: frozenset[str] = frozenset()
    allowed_sources: frozenset[SourceId]
    allowed_metrics_by_result: dict[str, tuple[MetricFact, ...]]
    ranked_customer_facts: dict[str, RankedCustomer]
    representative_journey_result_ids: frozenset[str] = frozenset()
    journey_event_facts: dict[str, JourneyEvent] = Field(default_factory=dict)
    signal_source_facts: dict[str, SourceId] = Field(default_factory=dict)
    evidence_source_facts: dict[str, SourceId] = Field(default_factory=dict)
    evidence_customer_facts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result_and_evidence_provenance(self) -> Self:
        required_tools = {
            "catalog_sources",
            "aggregate_events",
            "match_journey_pattern",
            "rank_customers",
        }
        tool_names = set(self.tool_result_ids)
        if not required_tools <= tool_names:
            raise ValueError("required tool result_id values are missing")
        optional_tools = tool_names - required_tools
        if optional_tools not in (set(), {"get_customer_journey", "get_evidence"}):
            raise ValueError("optional journey and evidence result_id values must appear together")

        result_ids = list(self.tool_result_ids.values())
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("tool result_id values must be unique")
        for tool_name, result_id in self.tool_result_ids.items():
            if not result_id.startswith(f"{tool_name}:"):
                raise ValueError(f"result_id is not bound to tool {tool_name}")
        if set(self.allowed_metrics_by_result) != set(result_ids):
            raise ValueError("metric facts must exist for every unique tool result_id")
        for metrics in self.allowed_metrics_by_result.values():
            semantics = [(metric.label, metric.unit) for metric in metrics]
            if len(semantics) != len(set(semantics)):
                raise ValueError("metric facts must have unique label and unit semantics")

        if not self.fetched_evidence_ids <= self.allowed_evidence_ids:
            raise ValueError("fetched evidence must be included in allowed evidence")
        if set(self.evidence_source_facts) != set(self.fetched_evidence_ids):
            raise ValueError("fetched evidence must have exact source associations")
        if set(self.evidence_customer_facts) != set(self.fetched_evidence_ids):
            raise ValueError("fetched evidence must have exact customer associations")
        if not set(self.evidence_source_facts.values()) <= self.allowed_sources:
            raise ValueError("fetched evidence source must be allowed")
        if not set(self.evidence_customer_facts.values()) <= self.allowed_customer_ids:
            raise ValueError("fetched evidence customer must be allowed")

        journey_result_id = self.tool_result_ids.get("get_customer_journey")
        allowed_journey_ids = {journey_result_id} if journey_result_id is not None else set()
        if not self.representative_journey_result_ids <= allowed_journey_ids:
            raise ValueError("representative journey result_id is not tool-bound")
        has_evidence_tool = "get_evidence" in self.tool_result_ids
        if has_evidence_tool != bool(self.fetched_evidence_ids):
            raise ValueError("get_evidence result_id and fetched evidence must appear together")
        return self


class LegacyRunnerOutcome(RunnerContract):
    """Compatibility outcome for the existing fixed Journey runners."""

    outcome_kind: Literal["legacy"] = "legacy"
    status: Literal["completed"] = "completed"
    report: InsightReport
    facts: RunFacts
    agent_mode: Literal["fixture", "gemini"] = "fixture"


class GenericRunnerOutcome(RunnerContract):
    """Validated generic outcome, including safe partial and non-result states."""

    outcome_kind: Literal["generic"] = "generic"
    status: RunStatus
    goal: AnalysisGoal | None = None
    clarification: ClarificationRequired | None = None
    unsupported: UnsupportedAnalysis | None = None
    plan: AnalysisPlan | None = None
    facts: list[AnalysisFact] = Field(default_factory=list, max_length=128)
    notes: list[AnalysisNote] = Field(default_factory=list, max_length=128)
    report: CustomerSignalReport | None = None
    limitations: list[str] = Field(default_factory=list, max_length=32)
    error: PublicRunError | None = None
    failed_step_id: str | None = Field(default=None, max_length=128)
    agent_mode: Literal["fixture", "gemini"]
    model: str | None = Field(default=None, max_length=128)

    @field_validator("limitations")
    @classmethod
    def require_public_limitations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("generic outcome limitations must be unique")
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("generic outcome limitations must be bounded and nonblank")
        return value

    @model_validator(mode="after")
    def bind_outcome_state(self) -> Self:
        if self.status == "awaiting_clarification":
            if self.clarification is None:
                raise ValueError("awaiting_clarification requires clarification")
            if any(
                (
                    self.goal,
                    self.unsupported,
                    self.plan,
                    self.facts,
                    self.notes,
                    self.report,
                    self.error,
                )
            ):
                raise ValueError("clarification outcome cannot contain analysis results")
        elif self.status == "degraded":
            if self.report is not None or self.facts or self.notes or not self.limitations:
                raise ValueError("degraded no-data outcome requires only a server limitation")
        elif self.status == "completed":
            if (
                self.goal is None
                or self.plan is None
                or not self.facts
                or not self.notes
                or self.report is None
                or self.error is not None
                or self.clarification is not None
                or self.unsupported is not None
            ):
                raise ValueError("completed generic outcome requires verified analysis results")
        elif self.status == "failed":
            if self.error is None or self.report is not None:
                raise ValueError("failed generic outcome requires a safe error and no report")
            if self.unsupported is not None and (
                self.error.code != "unsupported_analysis" or self.facts or self.notes
            ):
                raise ValueError("unsupported outcome cannot contain executed analysis results")
        else:
            raise ValueError("generic runner outcome must be terminal or awaiting clarification")
        if self.failed_step_id is not None:
            if self.plan is None or self.failed_step_id not in {
                step.step_id for step in self.plan.steps
            }:
                raise ValueError("failed_step_id must belong to the generic outcome Plan")
            if self.error is not None and self.error.step_id not in {
                None,
                self.failed_step_id,
            }:
                raise ValueError("failed_step_id must match the public error step_id")
        return self


type RunnerOutcome = Annotated[
    LegacyRunnerOutcome | GenericRunnerOutcome,
    Field(discriminator="outcome_kind"),
]


class StepModelContext(RunnerContract):
    goal: AnalysisGoal
    plan: AnalysisPlan
    step: AnalysisStep
    facts: list[AnalysisFact]
    current_fact: AnalysisFact


class SelectionContext(RunnerContract):
    goal: AnalysisGoal
    plan: AnalysisPlan
    completed_step_ids: frozenset[str]
    facts: list[AnalysisFact]


class ReportModelContext(RunnerContract):
    goal: AnalysisGoal
    plan: AnalysisPlan
    facts: list[AnalysisFact]
    notes: list[AnalysisNote]


class AnalysisRunner(Protocol):
    """Runtime-independent interface implemented by fixture and model runners."""

    async def run(
        self,
        request: RunRequest,
        *,
        emit: EventEmitter,
    ) -> RunnerOutcome: ...


class UnsupportedQuestionError(ValueError):
    """Raised when a question is outside the fixture runner's supported intent."""

    code = "unsupported_question"


class UnsupportedClaimError(ValueError):
    """Raised before publication when a report claim is absent from run facts."""

    code = "unsupported_claim"


_PRIVATE_ANALYSIS_EVENT_KEYS = frozenset(
    {
        "chain_of_thought",
        "internal_reasoning",
        "messages",
        "prompt",
        "provider_response",
        "raw_fields",
        "reasoning",
        "thoughts",
    }
)


def _assert_public_analysis_payload(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_").replace(" ", "_")
            if normalized in _PRIVATE_ANALYSIS_EVENT_KEYS:
                raise ValueError(f"generic event payload key is not public: {key}")
            _assert_public_analysis_payload(item)
    elif isinstance(value, list):
        for item in value:
            _assert_public_analysis_payload(item)


__all__ = [
    "AnalysisEvent",
    "AnalysisEventEmitter",
    "AnalysisEventType",
    "AnalysisRunner",
    "EventEmitter",
    "GenericRunnerOutcome",
    "LegacyRunnerOutcome",
    "MetricFact",
    "MetricFactValue",
    "ReportModelContext",
    "ReportValidator",
    "RunFacts",
    "RunRequest",
    "RunnerOutcome",
    "SelectionContext",
    "StepModelContext",
    "ToolName",
    "UnsupportedClaimError",
    "UnsupportedQuestionError",
]
