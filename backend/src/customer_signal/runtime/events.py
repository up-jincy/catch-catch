"""Small, public runner events safe to translate to SSE."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNote,
    AnalysisPlan,
    ClarificationRequired,
    PublicRunError,
)
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.reports import CustomerSignalReport
from customer_signal.domain.types import GenericPrimitiveName


type RunnerEventType = Literal[
    "plan",
    "tool_started",
    "tool_completed",
    "validating",
    "result",
    "error",
    "fallback",
]
type GenericRunnerEventType = Literal[
    "run_started",
    "goal_created",
    "clarification_required",
    "plan_created",
    "plan_revised",
    "step_started",
    "fact_created",
    "analysis_note_created",
    "step_completed",
    "report_validating",
    "result",
    "error",
    "done",
]


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "chain_of_thought",
        "internal_reasoning",
        "masked_customer_id",
        "messages",
        "prompt",
        "raw_fields",
        "reasoning",
        "records",
        "thoughts",
    }
)


def _assert_safe_keys(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_").replace(" ", "_")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"payload key is not public: {key}")
            _assert_safe_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_safe_keys(item)


class RunnerEvent(BaseModel):
    """A framework-neutral event without IDs, timestamps, or private model state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: RunnerEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_safe_payload(self) -> Self:
        _assert_safe_keys(self.payload)
        return self


class GenericEventContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunStartedPayload(GenericEventContract):
    status: Literal["running"]


class GoalCreatedPayload(GenericEventContract):
    goal: AnalysisGoal


class PlanCreatedPayload(GenericEventContract):
    plan: AnalysisPlan


class StepStartedPayload(GenericEventContract):
    step_id: str = Field(min_length=1, max_length=128)
    primitive: GenericPrimitiveName
    selection_reason: str = Field(min_length=1, max_length=500)
    started_at: AwareDatetime


class FactCreatedPayload(GenericEventContract):
    step_id: str = Field(min_length=1, max_length=128)
    fact: AnalysisFact

    @model_validator(mode="after")
    def bind_step(self) -> Self:
        if self.step_id != self.fact.step_id:
            raise ValueError("fact event step_id must equal Fact step_id")
        return self


class AnalysisNoteCreatedPayload(GenericEventContract):
    note: AnalysisNote


class StepCompletedPayload(GenericEventContract):
    step_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "degraded", "failed"]
    result_ids: list[str] = Field(default_factory=list, max_length=4)
    duration_ms: int = Field(ge=0, le=40_000)


class ReportValidatingPayload(GenericEventContract):
    fact_ids: list[str] = Field(default_factory=list, max_length=128)
    result_ids: list[str] = Field(default_factory=list, max_length=128)


class ResultPayload(GenericEventContract):
    agent_mode: Literal["fixture", "gemini"]
    report: CustomerSignalReport


class DonePayload(GenericEventContract):
    status: Literal["completed", "degraded", "failed"]
    limitations: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def bind_degraded_limitations(self) -> Self:
        if self.status == "degraded" and not self.limitations:
            raise ValueError("degraded done requires limitations")
        return self


class RunStartedEvent(GenericEventContract):
    type: Literal["run_started"]
    payload: RunStartedPayload


class GoalCreatedEvent(GenericEventContract):
    type: Literal["goal_created"]
    payload: GoalCreatedPayload


class ClarificationRequiredEvent(GenericEventContract):
    type: Literal["clarification_required"]
    payload: ClarificationRequired


class PlanCreatedEvent(GenericEventContract):
    type: Literal["plan_created"]
    payload: PlanCreatedPayload


class PlanRevisedEvent(GenericEventContract):
    type: Literal["plan_revised"]
    payload: PlanCreatedPayload


class StepStartedEvent(GenericEventContract):
    type: Literal["step_started"]
    payload: StepStartedPayload


class FactCreatedEvent(GenericEventContract):
    type: Literal["fact_created"]
    payload: FactCreatedPayload


class AnalysisNoteCreatedEvent(GenericEventContract):
    type: Literal["analysis_note_created"]
    payload: AnalysisNoteCreatedPayload


class StepCompletedEvent(GenericEventContract):
    type: Literal["step_completed"]
    payload: StepCompletedPayload


class ReportValidatingEvent(GenericEventContract):
    type: Literal["report_validating"]
    payload: ReportValidatingPayload


class ResultEvent(GenericEventContract):
    type: Literal["result"]
    payload: ResultPayload


class ErrorEvent(GenericEventContract):
    type: Literal["error"]
    payload: PublicRunError


class DoneEvent(GenericEventContract):
    type: Literal["done"]
    payload: DonePayload


type GenericRunnerEvent = Annotated[
    RunStartedEvent
    | GoalCreatedEvent
    | ClarificationRequiredEvent
    | PlanCreatedEvent
    | PlanRevisedEvent
    | StepStartedEvent
    | FactCreatedEvent
    | AnalysisNoteCreatedEvent
    | StepCompletedEvent
    | ReportValidatingEvent
    | ResultEvent
    | ErrorEvent
    | DoneEvent,
    Field(discriminator="type"),
]
GENERIC_EVENT_ADAPTER = TypeAdapter(GenericRunnerEvent)


def validate_generic_event(
    event_type: GenericRunnerEventType,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Validate and canonicalize one generic SSE payload before storage."""

    event = GENERIC_EVENT_ADAPTER.validate_json(
        json.dumps(
            {"type": event_type, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return event.payload.model_dump(mode="json")


__all__ = [
    "DonePayload",
    "GENERIC_EVENT_ADAPTER",
    "GenericRunnerEvent",
    "GenericRunnerEventType",
    "RunnerEvent",
    "RunnerEventType",
    "validate_generic_event",
]
