"""Verified, transport-neutral outputs of generic analysis primitives."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Annotated, Any, Literal, Self, TypeVar

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from customer_signal.domain.models import DomainModel, EventType
from customer_signal.domain.primitive_catalog import required_canonical_metrics
from customer_signal.domain.sources import EventScope, TimeRange
from customer_signal.domain.types import (
    DimensionValue,
    FiniteNumber,
    GenericPrimitiveName,
    SourceId,
)


type AnalysisNumber = StrictInt | FiniteNumber
type FactPayloadKind = GenericPrimitiveName

_SEMANTIC_FIELD_KEY = re.compile(r"^(?:(?P<source>[a-z][a-z0-9_]{1,63})\.)?[a-z][a-z0-9_]*$")


class FactContractModel(DomainModel):
    """Strict base for values crossing the primitive/Fact trust boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class FieldRef(FactContractModel):
    """A source-aware semantic field; never a physical/raw column name."""

    field: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    field_kind: Literal["canonical", "dimension", "measure"] = "canonical"
    source_id: SourceId | None = None


class ProcessingStats(FactContractModel):
    scanned_events: int = Field(ge=0, le=10_000)
    matched_events: int = Field(ge=0, le=10_000)
    returned_rows: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def require_bounded_counts(self) -> Self:
        if self.matched_events > self.scanned_events:
            raise ValueError("matched_events cannot exceed scanned_events")
        return self


class FactProvenance(FactContractModel):
    scope: EventScope
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    adapter_versions: dict[SourceId, str] = Field(min_length=1, max_length=32)
    manifest_versions: dict[SourceId, str] = Field(min_length=1, max_length=32)
    dataset_version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bind_versions_to_restricted_scope(self) -> Self:
        _require_unique(self.source_ids, "provenance source_ids")
        if self.source_ids != self.scope.source_ids:
            raise ValueError("provenance source_ids must equal restricted scope source_ids")
        expected = set(self.source_ids)
        if set(self.adapter_versions) != expected:
            raise ValueError("adapter_versions must exactly cover restricted scope sources")
        if set(self.manifest_versions) != expected:
            raise ValueError("manifest_versions must exactly cover restricted scope sources")
        if any(not value for value in self.adapter_versions.values()):
            raise ValueError("adapter versions must be nonblank")
        if any(not value for value in self.manifest_versions.values()):
            raise ValueError("manifest versions must be nonblank")
        return self


class AnalysisMetricFact(FactContractModel):
    metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=200)
    value: AnalysisNumber
    unit: str = Field(min_length=1, max_length=64)
    dimensions: dict[str, DimensionValue] = Field(default_factory=dict, max_length=16)

    @field_validator("dimensions")
    @classmethod
    def require_semantic_dimension_keys(
        cls, value: dict[str, DimensionValue]
    ) -> dict[str, DimensionValue]:
        return _require_semantic_dimensions(value)


class AnalysisSourceCatalogFact(FactContractModel):
    source_id: SourceId
    data_interval: TimeRange
    row_count: int = Field(ge=0, le=10_000_000_000)
    manifest_version: str = Field(min_length=1, max_length=128)


class AnalysisDistributionBucket(FactContractModel):
    dimensions: dict[str, DimensionValue] = Field(default_factory=dict, max_length=16)
    event_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: list[str]) -> list[str]:
        return _require_unique_nonblank(value, "distribution evidence_ids")

    @field_validator("dimensions")
    @classmethod
    def require_semantic_dimension_keys(
        cls, value: dict[str, DimensionValue]
    ) -> dict[str, DimensionValue]:
        return _require_semantic_dimensions(value)


class AnalysisQualityMetric(FactContractModel):
    field: FieldRef
    missing_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    missing_rate: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def require_possible_missing_count(self) -> Self:
        if self.missing_count > self.total_count:
            raise ValueError("missing_count cannot exceed total_count")
        return self


class AnalysisAggregateBucket(FactContractModel):
    dimensions: dict[str, DimensionValue] = Field(default_factory=dict, max_length=16)
    metrics: list[AnalysisMetricFact] = Field(min_length=1, max_length=32)
    event_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_stable_members(self) -> Self:
        _require_metric_order(self.metrics, "aggregate bucket metrics")
        _require_unique_nonblank(self.evidence_ids, "aggregate bucket evidence_ids")
        return self

    @field_validator("dimensions")
    @classmethod
    def require_semantic_dimension_keys(
        cls, value: dict[str, DimensionValue]
    ) -> dict[str, DimensionValue]:
        return _require_semantic_dimensions(value)


class AnalysisTimeBucket(FactContractModel):
    time_range: TimeRange
    dimensions: dict[str, DimensionValue] = Field(default_factory=dict, max_length=16)
    metrics: list[AnalysisMetricFact] = Field(min_length=1, max_length=32)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_stable_members(self) -> Self:
        _require_metric_order(self.metrics, "time bucket metrics")
        _require_unique_nonblank(self.evidence_ids, "time bucket evidence_ids")
        return self

    @field_validator("dimensions")
    @classmethod
    def require_semantic_dimension_keys(
        cls, value: dict[str, DimensionValue]
    ) -> dict[str, DimensionValue]:
        return _require_semantic_dimensions(value)


class AnalysisRepetitionMatch(FactContractModel):
    customer_id: str = Field(min_length=1, max_length=128)
    occurrence_count: int = Field(ge=2, le=10_000)
    window: TimeRange
    evidence_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: list[str]) -> list[str]:
        return _require_unique_nonblank(value, "repetition evidence_ids")


class AnalysisSequenceMatch(FactContractModel):
    customer_id: str = Field(min_length=1, max_length=128)
    matched_event_ids: list[str] = Field(min_length=2, max_length=16)
    window: TimeRange
    evidence_ids: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_unique_ordered_ids(self) -> Self:
        _require_unique_nonblank(self.matched_event_ids, "matched_event_ids")
        _require_unique_nonblank(self.evidence_ids, "sequence evidence_ids")
        return self


class AnalysisMetricDelta(FactContractModel):
    metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    baseline: AnalysisNumber
    comparison: AnalysisNumber
    delta: AnalysisNumber
    unit: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_exact_delta(self) -> Self:
        expected = self.comparison - self.baseline
        if type(expected) is not type(self.delta) or expected != self.delta:
            raise ValueError("delta must exactly equal comparison minus baseline")
        return self


class AnalysisSignal(FactContractModel):
    signal_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=200)
    contribution: AnalysisNumber
    metric_refs: list[str] = Field(min_length=1, max_length=32)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_stable_refs(self) -> Self:
        _require_unique_nonblank(self.metric_refs, "signal metric_refs")
        _require_unique_nonblank(self.evidence_ids, "signal evidence_ids")
        return self


class AnalysisRankedCustomer(FactContractModel):
    customer_id: str = Field(min_length=1, max_length=128)
    score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    signals: list[AnalysisSignal] = Field(default_factory=list, max_length=32)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_stable_signals(self) -> Self:
        keys = [signal.signal_key for signal in self.signals]
        _require_unique_nonblank(keys, "ranking signal keys")
        if keys != sorted(keys):
            raise ValueError("ranking signals must be sorted by signal_key")
        _require_unique_nonblank(self.evidence_ids, "ranking evidence_ids")
        return self


class AnalysisJourneyEvent(FactContractModel):
    event_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(min_length=1, max_length=128)
    source_id: SourceId
    occurred_at: AwareDatetime
    event_type: EventType
    action: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=200)
    outcome: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=1_000)


class AnalysisMaskedEvidence(FactContractModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    source_id: SourceId
    occurred_at: AwareDatetime
    masked_customer_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=1_000)


_REQUIRED_CANONICAL_METRICS: dict[FactPayloadKind, frozenset[str]] = (
    required_canonical_metrics()
)


class FactPayloadBase(FactContractModel):
    input_fact_ids: list[str] = Field(default_factory=list, max_length=6)
    processing: ProcessingStats
    provenance: FactProvenance
    metrics: list[AnalysisMetricFact] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_common_payload_contract(self) -> Self:
        _require_unique_nonblank(self.input_fact_ids, "input_fact_ids")
        _require_metric_order(self.metrics, "payload metrics")
        kind = getattr(self, "kind", None)
        if kind not in _REQUIRED_CANONICAL_METRICS:
            return self
        by_key = {metric.metric_key: metric for metric in self.metrics}
        missing = _REQUIRED_CANONICAL_METRICS[kind] - set(by_key)
        if missing:
            raise ValueError("payload is missing a canonical metric")
        if not by_key:
            raise ValueError("payload is missing a canonical metric")
        if self.processing.returned_rows == 0 and any(metric.value != 0 for metric in self.metrics):
            raise ValueError("zero-result payload canonical metrics must have value 0")
        return self


class CatalogSourcesPayload(FactPayloadBase):
    kind: Literal["catalog_sources"]
    sources: list[AnalysisSourceCatalogFact] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def require_source_order(self) -> Self:
        _require_sorted_unique(self.sources, lambda source: source.source_id, "catalog sources")
        return self


class ProfileEventsPayload(FactPayloadBase):
    kind: Literal["profile_events"]
    distributions: list[AnalysisDistributionBucket] = Field(default_factory=list, max_length=100)
    data_quality: list[AnalysisQualityMetric] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_profile_order(self) -> Self:
        _require_sorted_unique(
            self.distributions,
            lambda bucket: _dimension_key(bucket.dimensions),
            "profile distributions",
        )
        _require_sorted_unique(
            self.data_quality,
            lambda metric: (
                metric.field.source_id or "",
                metric.field.field_kind,
                metric.field.field,
            ),
            "profile data_quality",
        )
        return self


class AggregateEventsPayload(FactPayloadBase):
    kind: Literal["aggregate_events"]
    requested_metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    buckets: list[AnalysisAggregateBucket] = Field(default_factory=list, max_length=100)
    series: list[AnalysisTimeBucket] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_aggregate_order(self) -> Self:
        _require_exact_requested_metric(
            self.metrics, self.requested_metric_key, "aggregate requested metric"
        )
        _require_sorted_unique(
            self.buckets,
            lambda bucket: _dimension_key(bucket.dimensions),
            "aggregate buckets",
        )
        _require_sorted_unique(
            self.series,
            lambda bucket: (
                bucket.time_range.start_at,
                bucket.time_range.end_at,
                _dimension_key(bucket.dimensions),
            ),
            "aggregate series",
        )
        for nested in [*self.buckets, *self.series]:
            if any(metric.metric_key != self.requested_metric_key for metric in nested.metrics):
                raise ValueError("aggregate nested metrics must equal the requested metric key")
        return self


class SegmentCustomersPayload(FactPayloadBase):
    kind: Literal["segment_customers"]
    segment_id: str = Field(min_length=1, max_length=128)
    customer_ids: list[str] = Field(default_factory=list, max_length=100)
    predicate_counts: dict[str, int] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def require_segment_order(self) -> Self:
        _require_unique_nonblank(self.customer_ids, "segment customer_ids")
        if self.customer_ids != sorted(self.customer_ids):
            raise ValueError("segment customer_ids must be sorted")
        if any(not key or value < 0 for key, value in self.predicate_counts.items()):
            raise ValueError("predicate_counts must contain nonnegative semantic counts")
        return self


class RepetitionPayload(FactPayloadBase):
    kind: Literal["detect_repetition"]
    matches: list[AnalysisRepetitionMatch] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_repetition_order(self) -> Self:
        _require_unique_nonblank(
            [match.customer_id for match in self.matches], "repetition customer IDs"
        )
        _require_sorted_unique(
            self.matches,
            lambda match: (-match.occurrence_count, match.customer_id),
            "repetition matches",
        )
        return self


class SequenceMatchPayload(FactPayloadBase):
    kind: Literal["match_sequence"]
    matched_customer_ids: list[str] = Field(default_factory=list, max_length=100)
    matches: list[AnalysisSequenceMatch] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def bind_customer_request_order(self) -> Self:
        _require_unique_nonblank(self.matched_customer_ids, "matched_customer_ids")
        match_ids = [match.customer_id for match in self.matches]
        _require_unique_nonblank(match_ids, "sequence match customers")
        if self.matched_customer_ids != match_ids:
            raise ValueError("matched_customer_ids must equal Sequence match request order")
        _require_unique_nonblank(
            [event_id for match in self.matches for event_id in match.matched_event_ids],
            "sequence event_id values",
        )
        return self


class SegmentComparisonPayload(FactPayloadBase):
    kind: Literal["compare_segments"]
    requested_metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    baseline_fact_id: str = Field(min_length=1, max_length=128)
    comparison_fact_id: str = Field(min_length=1, max_length=128)
    deltas: list[AnalysisMetricDelta] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_comparison_order(self) -> Self:
        if self.baseline_fact_id == self.comparison_fact_id:
            raise ValueError("comparison Fact inputs must differ")
        if self.input_fact_ids != [self.baseline_fact_id, self.comparison_fact_id]:
            raise ValueError("comparison baseline/comparison must equal ordered input_fact_ids")
        _require_exact_requested_metric(
            self.metrics, self.requested_metric_key, "comparison requested metric"
        )
        if len(self.deltas) != 1:
            raise ValueError("comparison payload requires exactly one requested metric delta")
        delta = self.deltas[0]
        if self.requested_metric_key != f"{delta.metric_key}_delta":
            raise ValueError("comparison requested metric must bind its input metric delta")
        published = self.metrics[0]
        if (
            type(published.value) is not type(delta.delta)
            or published.value != delta.delta
            or published.unit != delta.unit
        ):
            raise ValueError("comparison published metric must exactly equal its delta")
        return self


class CustomerRankingPayload(FactPayloadBase):
    kind: Literal["rank_customers"]
    customers: list[AnalysisRankedCustomer] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_ranking_order(self) -> Self:
        _require_unique_nonblank(
            [customer.customer_id for customer in self.customers], "ranking customer IDs"
        )
        _require_sorted_unique(
            self.customers,
            lambda customer: (-customer.score, customer.customer_id),
            "ranked customers",
        )
        return self


class CustomerJourneyPayload(FactPayloadBase):
    kind: Literal["get_customer_journey"]
    customer_id: str = Field(min_length=1, max_length=128)
    events: list[AnalysisJourneyEvent] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_journey_order(self) -> Self:
        _require_unique_nonblank(
            [event.event_id for event in self.events], "journey event_id values"
        )
        _require_sorted_unique(
            self.events,
            lambda event: (event.occurred_at, event.event_id),
            "journey events",
        )
        evidence_ids = [event.evidence_id for event in self.events]
        _require_unique_nonblank(evidence_ids, "journey evidence_ids")
        return self


class EvidencePayload(FactPayloadBase):
    kind: Literal["get_evidence"]
    records: list[AnalysisMaskedEvidence] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_requested_evidence_order(self) -> Self:
        _require_unique_nonblank(
            [record.evidence_id for record in self.records], "evidence record IDs"
        )
        return self


type FactPayload = Annotated[
    CatalogSourcesPayload
    | ProfileEventsPayload
    | AggregateEventsPayload
    | SegmentCustomersPayload
    | RepetitionPayload
    | SequenceMatchPayload
    | SegmentComparisonPayload
    | CustomerRankingPayload
    | CustomerJourneyPayload
    | EvidencePayload,
    Field(discriminator="kind"),
]


class FactProjection(FactContractModel):
    customer_ids: list[str]
    evidence_ids: list[str]
    metrics: list[AnalysisMetricFact]


def extract_fact_projection(payload: FactPayload) -> FactProjection:
    """Derive the complete authorization projection from one typed payload."""

    customer_ids: list[str] = []
    evidence_ids: list[str] = []

    def customers(values: Iterable[str]) -> None:
        _extend_stable_unique(customer_ids, values)

    def evidence(values: Iterable[str]) -> None:
        _extend_stable_unique(evidence_ids, values)

    if isinstance(payload, ProfileEventsPayload):
        for bucket in payload.distributions:
            evidence(bucket.evidence_ids)
    elif isinstance(payload, AggregateEventsPayload):
        for bucket in payload.buckets:
            evidence(bucket.evidence_ids)
        for bucket in payload.series:
            evidence(bucket.evidence_ids)
    elif isinstance(payload, SegmentCustomersPayload):
        customers(payload.customer_ids)
    elif isinstance(payload, RepetitionPayload):
        for match in payload.matches:
            customers([match.customer_id])
            evidence(match.evidence_ids)
    elif isinstance(payload, SequenceMatchPayload):
        customers(payload.matched_customer_ids)
        for match in payload.matches:
            evidence(match.evidence_ids)
    elif isinstance(payload, CustomerRankingPayload):
        for customer in payload.customers:
            customers([customer.customer_id])
            evidence(customer.evidence_ids)
            for signal in customer.signals:
                evidence(signal.evidence_ids)
    elif isinstance(payload, CustomerJourneyPayload):
        customers([payload.customer_id])
        evidence(event.evidence_id for event in payload.events)
    elif isinstance(payload, EvidencePayload):
        evidence(record.evidence_id for record in payload.records)

    return FactProjection(
        customer_ids=customer_ids,
        evidence_ids=evidence_ids,
        metrics=list(payload.metrics),
    )


class AnalysisFact(FactContractModel):
    fact_id: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=128)
    primitive: GenericPrimitiveName
    result_id: str = Field(min_length=1, max_length=128)
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    customer_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    metrics: list[AnalysisMetricFact] = Field(min_length=1, max_length=32)
    payload: FactPayload
    created_at: AwareDatetime

    @model_validator(mode="after")
    def bind_to_payload_and_restricted_scope(self) -> Self:
        _require_unique(self.source_ids, "Fact source_ids")
        _require_unique_nonblank(self.customer_ids, "Fact customer_ids")
        _require_unique_nonblank(self.evidence_ids, "Fact evidence_ids")
        if self.primitive != self.payload.kind:
            raise ValueError("Fact primitive must equal payload kind")
        projection = extract_fact_projection(self.payload)
        if (
            self.customer_ids != projection.customer_ids
            or self.evidence_ids != projection.evidence_ids
            or self.metrics != projection.metrics
        ):
            raise ValueError("Fact top-level authorization must equal payload projection")
        provenance = self.payload.provenance
        if (
            self.source_ids != provenance.source_ids
            or self.source_ids != provenance.scope.source_ids
        ):
            raise ValueError("Fact sources must equal the restricted scope")
        nested_sources = _nested_source_ids(self.payload)
        if not nested_sources <= set(self.source_ids):
            raise ValueError("payload nested source is outside the restricted scope")
        return self

    def metric(self, metric_key: str) -> AnalysisMetricFact:
        matches = [metric for metric in self.metrics if metric.metric_key == metric_key]
        if len(matches) != 1:
            raise LookupError(f"metric {metric_key} does not resolve to exactly one Fact metric")
        return matches[0]


def build_fact(
    *,
    fact_id: str,
    step_id: str,
    primitive: GenericPrimitiveName,
    result_id: str,
    payload: FactPayload,
    scope: EventScope,
    created_at: AwareDatetime,
    input_facts: Sequence[AnalysisFact] | None = None,
) -> AnalysisFact:
    """Build a Fact whose public authorization is derived only from server payload data."""

    if payload.provenance.scope != scope or payload.provenance.source_ids != scope.source_ids:
        raise ValueError("payload provenance must equal the restricted scope")
    projection = extract_fact_projection(payload)
    fact = AnalysisFact(
        fact_id=fact_id,
        step_id=step_id,
        primitive=primitive,
        result_id=result_id,
        source_ids=list(scope.source_ids),
        customer_ids=projection.customer_ids,
        evidence_ids=projection.evidence_ids,
        metrics=projection.metrics,
        payload=payload,
        created_at=created_at,
    )
    if isinstance(payload, SegmentComparisonPayload):
        if input_facts is None:
            raise ValueError("comparison Fact build requires its ordered input Facts")
        validate_comparison_payload(payload, input_facts)
    return fact


def validate_comparison_payload(
    payload: SegmentComparisonPayload,
    input_facts: Sequence[AnalysisFact],
) -> None:
    """Bind a comparison delta to its two immutable input Fact metrics."""

    if [fact.fact_id for fact in input_facts] != payload.input_fact_ids:
        raise ValueError("comparison input Facts must equal ordered input_fact_ids")
    if len(input_facts) != 2:
        raise ValueError("comparison requires exactly two input Facts")
    baseline_fact, comparison_fact = input_facts
    delta = payload.deltas[0]
    try:
        baseline_metric = baseline_fact.metric(delta.metric_key)
        comparison_metric = comparison_fact.metric(delta.metric_key)
    except LookupError as error:
        raise ValueError("comparison input metric must resolve exactly in both Facts") from error
    if baseline_metric.unit != delta.unit or comparison_metric.unit != delta.unit:
        raise ValueError("comparison delta unit must equal both input Fact metric units")
    if (
        type(baseline_metric.value) is not type(delta.baseline)
        or baseline_metric.value != delta.baseline
        or type(comparison_metric.value) is not type(delta.comparison)
        or comparison_metric.value != delta.comparison
    ):
        raise ValueError("comparison delta values must equal ordered input Fact metrics")


T = TypeVar("T")
K = TypeVar("K")


def _require_unique(values: Sequence[Any], context: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{context} must be unique")


def _require_unique_nonblank(values: list[str], context: str) -> list[str]:
    if any(not value for value in values):
        raise ValueError(f"{context} must be nonblank")
    _require_unique(values, context)
    return values


def _require_sorted_unique(values: Sequence[T], key: Callable[[T], K], context: str) -> None:
    keys = [key(value) for value in values]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{context} must be unique")
    if keys != sorted(keys):
        raise ValueError(f"{context} must use stable canonical order")


def _metric_key(metric: AnalysisMetricFact) -> tuple[str, str]:
    return (metric.metric_key, _dimension_key(metric.dimensions))


def _require_metric_order(metrics: Sequence[AnalysisMetricFact], context: str) -> None:
    _require_sorted_unique(metrics, _metric_key, context)


def _require_exact_requested_metric(
    metrics: Sequence[AnalysisMetricFact], requested_metric_key: str, context: str
) -> None:
    if len(metrics) != 1 or metrics[0].metric_key != requested_metric_key:
        raise ValueError(f"{context} must publish exactly the requested metric key")


def _require_semantic_dimensions(
    dimensions: dict[str, DimensionValue],
) -> dict[str, DimensionValue]:
    if any(_SEMANTIC_FIELD_KEY.fullmatch(name) is None for name in dimensions):
        raise ValueError("dimension keys must be canonical semantic field references")
    return dimensions


def _dimension_key(dimensions: dict[str, DimensionValue]) -> str:
    return json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _extend_stable_unique(target: list[str], values: Iterable[str]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)


def _nested_source_ids(payload: FactPayload) -> set[SourceId]:
    source_ids = _metric_source_ids(payload.metrics)
    if isinstance(payload, CatalogSourcesPayload):
        source_ids.update(source.source_id for source in payload.sources)
    elif isinstance(payload, ProfileEventsPayload):
        source_ids.update(
            metric.field.source_id
            for metric in payload.data_quality
            if metric.field.source_id is not None
        )
        for bucket in payload.distributions:
            source_ids.update(_dimension_source_ids(bucket.dimensions))
    elif isinstance(payload, AggregateEventsPayload):
        for bucket in payload.buckets:
            source_ids.update(_dimension_source_ids(bucket.dimensions))
            source_ids.update(_metric_source_ids(bucket.metrics))
        for bucket in payload.series:
            source_ids.update(_dimension_source_ids(bucket.dimensions))
            source_ids.update(_metric_source_ids(bucket.metrics))
    elif isinstance(payload, CustomerRankingPayload):
        for customer in payload.customers:
            for signal in customer.signals:
                source_ids.update(_semantic_ref_source_ids(signal.metric_refs))
    elif isinstance(payload, CustomerJourneyPayload):
        source_ids.update(event.source_id for event in payload.events)
    elif isinstance(payload, EvidencePayload):
        source_ids.update(record.source_id for record in payload.records)
    return source_ids


def _dimension_source_ids(dimensions: dict[str, DimensionValue]) -> set[SourceId]:
    return _semantic_ref_source_ids(dimensions)


def _metric_source_ids(metrics: Sequence[AnalysisMetricFact]) -> set[SourceId]:
    return {
        source_id for metric in metrics for source_id in _dimension_source_ids(metric.dimensions)
    }


def _semantic_ref_source_ids(values: Iterable[str]) -> set[SourceId]:
    sources: set[SourceId] = set()
    for value in values:
        match = _SEMANTIC_FIELD_KEY.fullmatch(value)
        if match is not None and match.group("source") is not None:
            sources.add(match.group("source"))
    return sources


__all__ = [
    "AggregateEventsPayload",
    "AnalysisAggregateBucket",
    "AnalysisDistributionBucket",
    "AnalysisFact",
    "AnalysisJourneyEvent",
    "AnalysisMaskedEvidence",
    "AnalysisMetricDelta",
    "AnalysisMetricFact",
    "AnalysisQualityMetric",
    "AnalysisRankedCustomer",
    "AnalysisRepetitionMatch",
    "AnalysisSequenceMatch",
    "AnalysisSignal",
    "AnalysisSourceCatalogFact",
    "AnalysisTimeBucket",
    "CatalogSourcesPayload",
    "CustomerJourneyPayload",
    "CustomerRankingPayload",
    "EvidencePayload",
    "FactPayload",
    "FactPayloadBase",
    "FactPayloadKind",
    "FactProjection",
    "FactProvenance",
    "FieldRef",
    "ProcessingStats",
    "ProfileEventsPayload",
    "RepetitionPayload",
    "SegmentComparisonPayload",
    "SegmentCustomersPayload",
    "SequenceMatchPayload",
    "build_fact",
    "extract_fact_projection",
    "validate_comparison_payload",
]
