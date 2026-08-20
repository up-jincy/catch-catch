"""Strict, dependency-free inputs for generic customer-signal primitives."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, TypeAdapter, model_validator


class PrimitiveContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CatalogSourcesInput(PrimitiveContract):
    primitive: Literal["catalog_sources"]


class ProfileEventsInput(PrimitiveContract):
    primitive: Literal["profile_events"]
    group_by: list[str] = Field(default_factory=list, max_length=16)
    predicates: list[str] = Field(default_factory=list, max_length=32)


class AggregateEventsInput(PrimitiveContract):
    primitive: Literal["aggregate_events"]
    aggregation: Literal["count", "sum", "avg", "min", "max"]
    measure: str | None = Field(default=None, min_length=1, max_length=128)
    group_by: list[str] = Field(default_factory=list, max_length=16)
    predicates: list[str] = Field(default_factory=list, max_length=32)
    time_grain: Literal["hour", "day", "week", "month"]

    @model_validator(mode="after")
    def validate_measure_for_aggregation(self):
        if self.aggregation == "count" and self.measure is not None:
            raise ValueError("count aggregation does not accept a measure")
        if self.aggregation != "count" and self.measure is None:
            raise ValueError("numeric aggregation requires a measure")
        return self


class SegmentCustomersInput(PrimitiveContract):
    primitive: Literal["segment_customers"]
    predicates: list[str] = Field(min_length=1, max_length=32)
    minimum_matching_events: int = Field(default=1, ge=1, le=10_000)


class DetectRepetitionInput(PrimitiveContract):
    primitive: Literal["detect_repetition"]
    topic_field: str = Field(min_length=1, max_length=128)
    minimum_occurrences: int = Field(ge=2, le=10_000)
    within_hours: int = Field(ge=1, le=24 * 365)


class MatchSequenceInput(PrimitiveContract):
    primitive: Literal["match_sequence"]
    sequence: list[str] = Field(min_length=2, max_length=16)


class CompareSegmentsInput(PrimitiveContract):
    primitive: Literal["compare_segments"]
    metric_key: str = Field(min_length=1, max_length=128)


class RankCustomersInput(PrimitiveContract):
    primitive: Literal["rank_customers"]
    weights: dict[str, FiniteFloat] = Field(min_length=1, max_length=32)
    limit: int = Field(default=10, ge=1, le=100)


class GetCustomerJourneyInput(PrimitiveContract):
    primitive: Literal["get_customer_journey"]
    limit: int = Field(default=20, ge=1, le=100)


class GetEvidenceInput(PrimitiveContract):
    primitive: Literal["get_evidence"]
    limit: int = Field(default=20, ge=1, le=100)


type PrimitiveInput = Annotated[
    CatalogSourcesInput
    | ProfileEventsInput
    | AggregateEventsInput
    | SegmentCustomersInput
    | DetectRepetitionInput
    | MatchSequenceInput
    | CompareSegmentsInput
    | RankCustomersInput
    | GetCustomerJourneyInput
    | GetEvidenceInput,
    Field(discriminator="primitive"),
]

PRIMITIVE_INPUT_ADAPTER = TypeAdapter(PrimitiveInput)


__all__ = [
    "AggregateEventsInput",
    "CatalogSourcesInput",
    "CompareSegmentsInput",
    "DetectRepetitionInput",
    "GetCustomerJourneyInput",
    "GetEvidenceInput",
    "MatchSequenceInput",
    "PRIMITIVE_INPUT_ADAPTER",
    "PrimitiveInput",
    "ProfileEventsInput",
    "RankCustomersInput",
    "SegmentCustomersInput",
]
