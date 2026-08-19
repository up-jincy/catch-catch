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

from customer_signal.domain.models import SourceId
from customer_signal.domain.reports import InsightReport, JourneyEvent, RankedCustomer
from customer_signal.runtime.events import RunnerEvent


type MetricFactValue = FiniteFloat | int | str
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
    enabled_sources: list[SourceId] = Field(min_length=1, max_length=3)

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


class RunFacts(RunnerContract):
    """Claims authorized by structured tool results from one run."""

    tool_result_ids: dict[str, str]
    allowed_customer_ids: frozenset[str]
    allowed_evidence_ids: frozenset[str]
    allowed_sources: frozenset[SourceId]
    allowed_metric_values_by_result: dict[str, tuple[MetricFactValue, ...]]
    ranked_customer_facts: dict[str, RankedCustomer]
    representative_journey_result_ids: frozenset[str] = frozenset()
    journey_event_facts: dict[str, JourneyEvent] = Field(default_factory=dict)
    signal_source_facts: dict[str, SourceId] = Field(default_factory=dict)


class RunnerOutcome(RunnerContract):
    """Validated report and the facts needed by downstream run-scoped APIs."""

    report: InsightReport
    facts: RunFacts
    agent_mode: Literal["fixture"] = "fixture"


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
    "MetricFactValue",
    "ReportValidator",
    "RunFacts",
    "RunRequest",
    "RunnerOutcome",
    "UnsupportedClaimError",
    "UnsupportedQuestionError",
]
