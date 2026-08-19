"""Canonical customer-event and evidence contracts."""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def validate_dataset_invariants(self) -> Self:
        if len(self.customers) != len(set(self.customers)):
            raise ValueError("customers must be unique")
        if len(self.ground_truth_customer_ids) != len(set(self.ground_truth_customer_ids)):
            raise ValueError("ground_truth_customer_ids must be unique")

        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_id values must be unique")

        event_evidence_ids = [event.evidence_id for event in self.events]
        if len(event_evidence_ids) != len(set(event_evidence_ids)):
            raise ValueError("event evidence_id references must be unique")

        evidence_ids = [record.evidence_id for record in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")

        customer_ids = set(self.customers)
        if not set(self.ground_truth_customer_ids) <= customer_ids:
            raise ValueError("ground truth customers must belong to customers")
        if any(event.canonical_customer_id not in customer_ids for event in self.events):
            raise ValueError("event customers must belong to customers")

        evidence_by_id = {record.evidence_id: record for record in self.evidence}
        if any(evidence_id not in evidence_by_id for evidence_id in event_evidence_ids):
            raise ValueError("every event evidence_id must exist")
        if set(evidence_by_id) - set(event_evidence_ids):
            raise ValueError("evidence records must not be orphaned")

        for event in self.events:
            evidence = evidence_by_id[event.evidence_id]
            if event.source_id != evidence.source_id:
                raise ValueError("event and evidence source_id must align")
            if event.occurred_at != evidence.occurred_at:
                raise ValueError("event and evidence occurred_at must align")
        return self
