"""Strict contracts shared by analysis runners and the runtime coordinator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from customer_signal.domain.types import PrimitiveName, SourceId
from customer_signal.domain.reports import InsightReport, JourneyEvent, RankedCustomer
from customer_signal.runtime.events import RunnerEvent


type MetricFactValue = FiniteFloat | int | str
type ToolName = PrimitiveName
type EventEmitter = Callable[[RunnerEvent], Awaitable[None] | None]
type ReportValidator = Callable[[InsightReport, "RunFacts"], InsightReport | None]


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


class RunnerOutcome(RunnerContract):
    """Validated report and the facts needed by downstream run-scoped APIs."""

    report: InsightReport
    facts: RunFacts
    agent_mode: Literal["fixture", "gemini"] = "fixture"


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


__all__ = [
    "AnalysisRunner",
    "EventEmitter",
    "MetricFact",
    "MetricFactValue",
    "ReportValidator",
    "RunFacts",
    "RunRequest",
    "RunnerOutcome",
    "ToolName",
    "UnsupportedClaimError",
    "UnsupportedQuestionError",
]
