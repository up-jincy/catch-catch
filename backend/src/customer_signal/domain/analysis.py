"""Generic goal, bounded plan, claim, and public analysis-note contracts."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from customer_signal.domain.facts import FieldRef
from customer_signal.domain.models import DomainModel
from customer_signal.domain.primitives import PrimitiveInput
from customer_signal.domain.sources import TimeRange
from customer_signal.domain.types import (
    DimensionValue,
    FiniteNumber,
    GenericPrimitiveName,
    MeasureValue,
    SourceId,
)


type OutputKind = Literal[
    "profile",
    "aggregate",
    "segment",
    "comparison",
    "ranking",
    "journey",
    "evidence",
]
type AggregationKind = Literal["count", "distinct_count", "sum", "avg", "min", "max", "rate"]
type PredicateOperator = Literal[
    "eq",
    "ne",
    "lt",
    "lte",
    "gt",
    "gte",
    "in",
    "not_in",
    "contains",
    "is_null",
]
type PredicateScalar = DimensionValue | MeasureValue
type PredicateValue = PredicateScalar | list[PredicateScalar]
type ClaimTarget = StrictStr | StrictInt | FiniteNumber | StrictBool | list[StrictStr]
type ClaimId = Annotated[str, Field(pattern=r"^claim-[a-f0-9]{24}$")]
type PublicExplanation = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
type RunStatus = Literal[
    "queued",
    "running",
    "awaiting_clarification",
    "completed",
    "degraded",
    "failed",
]


class AnalysisContractModel(DomainModel):
    """Strict base for all model-authored generic analysis values."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PopulationSpec(AnalysisContractModel):
    entity: Literal["customers"] = "customers"
    description: str = Field(min_length=1, max_length=500)


class MeasureSpec(AnalysisContractModel):
    metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=200)
    aggregation: AggregationKind
    field: FieldRef | None = None
    unit: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_numeric_field_for_numeric_aggregation(self) -> Self:
        if self.aggregation in {"sum", "avg", "min", "max"} and self.field is None:
            raise ValueError("numeric aggregation requires a semantic field")
        return self


class Predicate(AnalysisContractModel):
    field: FieldRef
    operator: PredicateOperator
    value: PredicateValue | None = None

    @model_validator(mode="after")
    def bind_null_operator(self) -> Self:
        if self.operator == "is_null" and self.value is not None:
            raise ValueError("is_null predicate does not accept a value")
        if self.operator != "is_null" and self.value is None:
            raise ValueError("predicate operator requires a value")
        if self.operator in {"in", "not_in"} and not isinstance(self.value, list):
            raise ValueError("set predicate requires a list value")
        if self.operator not in {"in", "not_in"} and isinstance(self.value, list):
            raise ValueError("scalar predicate does not accept a list value")
        return self


class SequenceSpec(AnalysisContractModel):
    steps: list[str] = Field(min_length=2, max_length=16)
    within_hours: int = Field(ge=1, le=24 * 365)

    @model_validator(mode="after")
    def require_nonblank_steps(self) -> Self:
        if any(not step.strip() for step in self.steps):
            raise ValueError("sequence steps must be nonblank")
        return self


class AnalysisGoal(AnalysisContractModel):
    kind: Literal["goal"] = "goal"
    goal_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=1_000)
    population: PopulationSpec
    time_range: TimeRange
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    measures: list[MeasureSpec] = Field(min_length=1, max_length=16)
    group_by: list[FieldRef] = Field(default_factory=list, max_length=16)
    predicates: list[Predicate] = Field(default_factory=list, max_length=32)
    sequence: SequenceSpec | None = None
    output: OutputKind

    @model_validator(mode="after")
    def require_unique_goal_inputs(self) -> Self:
        _require_unique(self.source_ids, "goal source_ids")
        _require_unique([measure.metric_key for measure in self.measures], "goal metric keys")
        _require_unique(
            [_canonical_model_key(field) for field in self.group_by], "goal group_by fields"
        )
        return self


class ClarificationRequired(AnalysisContractModel):
    kind: Literal["clarification"] = "clarification"
    clarification_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=500)


class UnsupportedAnalysis(AnalysisContractModel):
    kind: Literal["unsupported"] = "unsupported"
    code: Literal[
        "pii_request",
        "raw_export",
        "write_request",
        "unsupported_statistic",
        "out_of_scope",
    ]
    reason: str = Field(min_length=1, max_length=500)
    suggested_questions: list[str] = Field(min_length=1, max_length=3)

    @field_validator("suggested_questions")
    @classmethod
    def require_unique_suggestions(cls, value: list[str]) -> list[str]:
        _require_unique(value, "suggested questions")
        if any(not suggestion.strip() for suggestion in value):
            raise ValueError("suggested questions must be nonblank")
        return value


type GoalDecision = Annotated[
    AnalysisGoal | ClarificationRequired | UnsupportedAnalysis,
    Field(discriminator="kind"),
]


class StepLimits(AnalysisContractModel):
    max_input_events: int = Field(ge=1, le=10_000)
    max_output_rows: int = Field(ge=1, le=100)
    max_evidence: int = Field(ge=0, le=20)
    timeout_seconds: Annotated[float, Field(gt=0, le=40, allow_inf_nan=False)]


class ExpectedOutputSpec(AnalysisContractModel):
    payload_kind: GenericPrimitiveName
    required_metric_keys: list[str] = Field(min_length=1, max_length=32)

    @field_validator("required_metric_keys")
    @classmethod
    def require_stable_metric_keys(cls, value: list[str]) -> list[str]:
        _require_unique(value, "required metric keys")
        if any(not key or len(key) > 64 for key in value):
            raise ValueError("required metric keys must be bounded and nonblank")
        if value != sorted(value):
            raise ValueError("required metric keys must use stable canonical order")
        return value


class ContinueAfterStep(AnalysisContractModel):
    kind: Literal["continue"] = "continue"


class StopOnEmpty(AnalysisContractModel):
    kind: Literal["stop_on_empty"] = "stop_on_empty"


class StopOnMetric(AnalysisContractModel):
    kind: Literal["stop_on_metric"] = "stop_on_metric"
    metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    operator: Literal["eq", "lt", "lte", "gt", "gte"]
    target: StrictInt | FiniteNumber


type StopCondition = Annotated[
    ContinueAfterStep | StopOnEmpty | StopOnMetric,
    Field(discriminator="kind"),
]


class AnalysisStep(AnalysisContractModel):
    step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    primitive: GenericPrimitiveName
    parameters: PrimitiveInput
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    input_step_ids: list[str] = Field(default_factory=list, max_length=4)
    expected_output: ExpectedOutputSpec
    stop_condition: StopCondition
    limits: StepLimits
    selection_reason: PublicExplanation = "분석 목표를 달성하기 위해 이 단계를 선택했습니다."

    @model_validator(mode="after")
    def bind_step_contract(self) -> Self:
        _require_unique(self.source_ids, "step source_ids")
        _require_unique(self.input_step_ids, "step input_step_ids")
        if self.parameters.primitive != self.primitive:
            raise ValueError("parameters primitive must equal step primitive")
        if self.expected_output.payload_kind != self.primitive:
            raise ValueError("expected payload kind must equal step primitive")
        if (
            isinstance(self.stop_condition, StopOnMetric)
            and self.stop_condition.metric_key not in self.expected_output.required_metric_keys
        ):
            raise ValueError("StopOnMetric metric_key must be a required metric key")
        return self


_DEPENDENCY_ARITY: dict[GenericPrimitiveName, tuple[int, int]] = {
    "catalog_sources": (0, 0),
    "profile_events": (0, 0),
    "aggregate_events": (0, 0),
    "segment_customers": (0, 0),
    "detect_repetition": (0, 0),
    "match_sequence": (0, 0),
    "compare_segments": (2, 2),
    "rank_customers": (1, 4),
    "get_customer_journey": (1, 1),
    "get_evidence": (1, 1),
}


class AnalysisPlan(AnalysisContractModel):
    plan_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    goal_id: str = Field(min_length=1, max_length=128)
    steps: list[AnalysisStep] = Field(min_length=3, max_length=6)
    rationale: PublicExplanation = "요청한 분석 목표를 달성하기 위한 실행 계획입니다."

    @model_validator(mode="after")
    def require_bounded_topological_plan(self) -> Self:
        step_ids = [step.step_id for step in self.steps]
        _require_unique(step_ids, "step_id values")
        prior: set[str] = set()
        for step in self.steps:
            if any(dependency not in prior for dependency in step.input_step_ids):
                raise ValueError("input_step_ids must reference prior steps only")
            minimum, maximum = _DEPENDENCY_ARITY[step.primitive]
            if not minimum <= len(step.input_step_ids) <= maximum:
                raise ValueError(
                    f"{step.primitive} dependency arity must be between {minimum} and {maximum}"
                )
            prior.add(step.step_id)
        return self


class PublicRunError(AnalysisContractModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    step_id: str | None = Field(default=None, max_length=128)
    suggested_questions: list[str] = Field(default_factory=list, max_length=3)


class FactRef(AnalysisContractModel):
    fact_id: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(default=0, ge=0)
    result_id: str | None = Field(default=None, min_length=1, max_length=128)
    metric_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str | None = Field(default=None, min_length=1, max_length=200)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    dimensions: dict[str, DimensionValue] | None = None
    segment_id: str | None = Field(default=None, min_length=1, max_length=128)
    customer_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_id: SourceId | None = None
    evidence_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_typed_selector(self) -> Self:
        selectors = (
            self.metric_key,
            self.segment_id,
            self.customer_id,
            self.source_id,
            self.evidence_id,
        )
        if not any(selector is not None for selector in selectors):
            raise ValueError(
                "FactRef requires a metric, Segment, customer, source, or evidence selector"
            )
        if (self.label is not None or self.unit is not None or self.dimensions is not None) and (
            self.metric_key is None
        ):
            raise ValueError("metric label, unit, and dimensions require metric_key")
        return self


type ClaimType = Literal["metric", "segment", "customer", "source", "evidence"]
type ClaimOperator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "contains", "in"]


class ClaimDraft(AnalysisContractModel):
    claim_type: ClaimType
    subject: str = Field(min_length=1, max_length=128)
    operator: ClaimOperator
    target: ClaimTarget
    fact_refs: list[FactRef] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def require_unique_fact_refs(self) -> Self:
        _require_unique(
            [_canonical_model_key(reference) for reference in self.fact_refs], "claim Fact refs"
        )
        return self


class VerifiedClaim(ClaimDraft):
    claim_id: ClaimId
    rendered_text: str = Field(min_length=1, max_length=500)


class AnalysisNoteDraft(AnalysisContractModel):
    step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=32)
    next_step_id: str | None = Field(default=None, pattern=r"^step-[a-z0-9-]+$", max_length=128)
    limitations: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(cls, value: list[str]) -> list[str]:
        _require_unique(value, "note limitations")
        if any(not limitation.strip() or len(limitation) > 500 for limitation in value):
            raise ValueError("note limitations must be bounded and nonblank")
        return value


class AnalysisNote(AnalysisContractModel):
    note_id: str = Field(pattern=r"^note-[a-f0-9]{24}$")
    step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    status: Literal["completed"] = "completed"
    objective: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(min_length=1, max_length=4)
    claims: list[VerifiedClaim] = Field(default_factory=list, max_length=32)
    next_step_id: str | None = Field(default=None, pattern=r"^step-[a-z0-9-]+$", max_length=128)
    next_action: PublicExplanation = "현재 단계의 검증 결과를 기록했습니다."
    limitations: list[str] = Field(default_factory=list, max_length=16)
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    result_ids: list[str] = Field(min_length=1, max_length=4)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    started_at: AwareDatetime
    completed_at: AwareDatetime
    duration_ms: int = Field(ge=0, le=40_000)
    plan_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def require_server_publication_invariants(self) -> Self:
        for values, context in (
            (self.fact_ids, "note fact_ids"),
            ([claim.claim_id for claim in self.claims], "note claim_ids"),
            (self.source_ids, "note source_ids"),
            (self.result_ids, "note result_ids"),
            (self.evidence_ids, "note evidence_ids"),
            (self.limitations, "note limitations"),
        ):
            _require_unique(values, context)
        if self.completed_at < self.started_at:
            raise ValueError("note completed_at must not precede started_at")
        if any(
            reference.plan_revision != self.plan_revision
            for claim in self.claims
            for reference in claim.fact_refs
        ):
            raise ValueError("verified Claim revision must equal Note plan revision")
        return self


class ContinueSelection(AnalysisContractModel):
    kind: Literal["continue"] = "continue"
    next_step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    reason: PublicExplanation = "분석 계획의 다음 단계를 계속 실행합니다."


class StopSelection(AnalysisContractModel):
    kind: Literal["stop"] = "stop"
    reason: PublicExplanation = "검증 가능한 분석 단계를 모두 완료했습니다."


class ReviseSelection(AnalysisContractModel):
    kind: Literal["revise"] = "revise"
    revised_plan: AnalysisPlan
    next_step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    reason: PublicExplanation = "검증 결과에 맞춰 분석 계획을 수정합니다."

    @model_validator(mode="after")
    def require_selected_revised_step(self) -> Self:
        if self.next_step_id not in {step.step_id for step in self.revised_plan.steps}:
            raise ValueError("next_step_id must belong to revised_plan")
        return self


type StepSelection = Annotated[
    ContinueSelection | StopSelection | ReviseSelection,
    Field(discriminator="kind"),
]


class RecommendedActionDraft(AnalysisContractModel):
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    claim_refs: list[ClaimId] = Field(min_length=1, max_length=32)
    fact_refs: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_unique_action_refs(self) -> Self:
        _require_unique(self.claim_refs, "recommended action claim_refs")
        _require_unique(self.fact_refs, "recommended action fact_refs")
        return self


class CustomerSignalReportDraft(AnalysisContractModel):
    goal_id: str = Field(min_length=1, max_length=128)
    claim_refs: list[ClaimId] = Field(default_factory=list, max_length=32)
    recommended_actions: list[RecommendedActionDraft] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def require_unique_verified_claim_refs(self) -> Self:
        _require_unique(self.claim_refs, "report draft claim_refs")
        action_claims = [
            claim_ref for action in self.recommended_actions for claim_ref in action.claim_refs
        ]
        if not set(action_claims) <= set(self.claim_refs):
            raise ValueError("recommended actions may reference only report verified claims")
        return self


def _require_unique(values: list[object], context: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{context} must be unique")


def _canonical_model_key(value: DomainModel) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


__all__ = [
    "AnalysisGoal",
    "AnalysisNote",
    "AnalysisNoteDraft",
    "AnalysisPlan",
    "AnalysisStep",
    "ClaimDraft",
    "ClaimId",
    "ClaimOperator",
    "ClaimTarget",
    "ClaimType",
    "ClarificationRequired",
    "ContinueAfterStep",
    "ContinueSelection",
    "CustomerSignalReportDraft",
    "ExpectedOutputSpec",
    "FactRef",
    "FieldRef",
    "GoalDecision",
    "MeasureSpec",
    "OutputKind",
    "PopulationSpec",
    "Predicate",
    "PublicExplanation",
    "PublicRunError",
    "RecommendedActionDraft",
    "ReviseSelection",
    "RunStatus",
    "SequenceSpec",
    "StepLimits",
    "StepSelection",
    "StopCondition",
    "StopOnEmpty",
    "StopOnMetric",
    "StopSelection",
    "UnsupportedAnalysis",
    "VerifiedClaim",
]
