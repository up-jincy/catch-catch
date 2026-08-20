"""Canonical customer-event, identity-provenance, and evidence contracts."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from customer_signal.domain.types import SourceId


type Scalar = str | int | float | bool | None
type EventType = Literal["search", "feedback", "digital_behavior", "subscription", "voc"]
type IdentityLinkType = Literal["EXACT", "DECLARED", "SYNTHETIC"]
type IdentityConfidence = Annotated[FiniteFloat, Field(ge=0, le=1)]


class DomainModel(BaseModel):
    """Shared strict base for portable, JSON-serializable domain contracts."""

    model_config = ConfigDict(extra="forbid")


class IdentityRef(DomainModel):
    """A source-native identifier participating in the customer identity graph."""

    namespace: str = Field(min_length=1)
    value: str = Field(min_length=1)


class IdentityEdge(DomainModel):
    """An explicit, provenance-bearing link between two identity nodes."""

    left: IdentityRef
    right: IdentityRef
    link_type: IdentityLinkType
    confidence: IdentityConfidence
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def reject_self_link(self) -> Self:
        if self.left == self.right:
            raise ValueError("identity edge endpoints must differ")
        return self


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
    identities: list[IdentityRef] = Field(default_factory=list)
    canonical_customer_id: str
    attributes: dict[str, Scalar] = Field(default_factory=dict)
    dimensions: dict[str, Scalar] = Field(default_factory=dict)
    measures: dict[str, FiniteFloat] = Field(default_factory=dict)


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
    identity_edges: list[IdentityEdge]
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

        edge_keys = [
            (
                edge.left.namespace,
                edge.left.value,
                edge.right.namespace,
                edge.right.value,
                edge.link_type,
            )
            for edge in self.identity_edges
        ]
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("identity edges must be unique")

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

        graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for edge in self.identity_edges:
            left = (edge.left.namespace, edge.left.value)
            right = (edge.right.namespace, edge.right.value)
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)

        canonical_nodes = {
            ("canonical_customer", customer_id) for customer_id in self.customers
        }
        for event in self.events:
            if not event.identities:
                raise ValueError("every event must include a source identity")
            pending = [
                (identity.namespace, identity.value) for identity in event.identities
            ]
            visited: set[tuple[str, str]] = set()
            while pending:
                node = pending.pop()
                if node in visited:
                    continue
                visited.add(node)
                pending.extend(graph.get(node, set()) - visited)
            resolved = visited & canonical_nodes
            expected = {("canonical_customer", event.canonical_customer_id)}
            if resolved != expected:
                raise ValueError(
                    "event identities must resolve to exactly one canonical customer"
                )
        return self


CanonicalCustomerEvent = CustomerEvent
