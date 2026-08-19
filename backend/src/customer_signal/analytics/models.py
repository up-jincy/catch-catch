"""Strict, portable result contracts for deterministic analytics operations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from customer_signal.data.repository import SourceCatalogEntry
from customer_signal.domain.models import EvidenceRecord, SourceId
from customer_signal.domain.reports import JourneyEvent, RankedCustomer


type AggregateDimension = Literal["source", "topic", "outcome"]


class AnalyticsResultModel(BaseModel):
    """Shared strict base for JSON-serializable analytics results."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ToolStats(AnalyticsResultModel):
    """Bounded row counts for one analytics operation."""

    scanned_rows: int = Field(ge=0)
    returned_rows: int = Field(ge=0)


class CatalogSourcesResult(AnalyticsResultModel):
    """Available canonical sources in a requested half-open time window."""

    result_id: str
    sources: list[SourceCatalogEntry] = Field(default_factory=list)
    missing_sources: list[SourceId] = Field(default_factory=list)
    stats: ToolStats


class AggregateBucket(AnalyticsResultModel):
    """One deterministic value bucket for the requested aggregate dimension."""

    value: str
    event_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    evidence_ids: list[str] = Field(default_factory=list)


class AggregateResult(AnalyticsResultModel):
    """Event aggregation grouped by source, topic, or outcome."""

    result_id: str
    group_by: AggregateDimension
    buckets: list[AggregateBucket] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stats: ToolStats


class PatternMatchResult(AnalyticsResultModel):
    """Customers satisfying the required ordered journey signals."""

    result_id: str
    candidate_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    customer_ids: list[str] = Field(default_factory=list)
    customers: list[RankedCustomer] = Field(default_factory=list)
    missing_sources: list[SourceId] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stats: ToolStats


class RankCustomersResult(AnalyticsResultModel):
    """Deterministically ordered scored candidates."""

    result_id: str
    candidate_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    customers: list[RankedCustomer] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stats: ToolStats


class CustomerJourneyResult(AnalyticsResultModel):
    """A display-safe chronological customer timeline."""

    result_id: str
    customer_id: str
    events: list[JourneyEvent] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stats: ToolStats


class EvidenceResult(AnalyticsResultModel):
    """Masked evidence records in requested identifier order."""

    result_id: str
    records: list[EvidenceRecord] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stats: ToolStats


__all__ = [
    "AggregateBucket",
    "AggregateDimension",
    "AggregateResult",
    "CatalogSourcesResult",
    "CustomerJourneyResult",
    "EvidenceResult",
    "PatternMatchResult",
    "RankCustomersResult",
    "ToolStats",
]
