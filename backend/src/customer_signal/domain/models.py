"""Canonical customer-event and evidence contracts."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


type Scalar = str | int | float | bool | None
type SourceId = Literal["search_history", "search_feedback", "voc"]
type EventType = Literal["search", "feedback", "voc"]


class DomainModel(BaseModel):
    """Shared strict base for portable, JSON-serializable domain contracts."""

    model_config = ConfigDict(extra="forbid")


class CustomerEvent(DomainModel):
    """A source-independent event in a canonical customer journey."""

    event_id: str
    evidence_id: str
    source_id: SourceId
    occurred_at: AwareDatetime
    event_type: EventType
    action: str
    topic: str
    outcome: str
    text: str
    canonical_customer_id: str
    attributes: dict[str, Scalar] = Field(default_factory=dict)


class EvidenceRecord(DomainModel):
    """A masked source record supporting one canonical event."""

    evidence_id: str
    source_id: SourceId
    occurred_at: AwareDatetime
    masked_customer_id: str
    summary: str
    raw_fields: dict[str, Scalar] = Field(default_factory=dict)


class SyntheticDataset(DomainModel):
    """Generated canonical events, masked evidence, and evaluation truth."""

    customers: list[str]
    events: list[CustomerEvent]
    evidence: list[EvidenceRecord]
    ground_truth_customer_ids: list[str]
