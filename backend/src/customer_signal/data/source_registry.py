"""Dynamic source registration and one-pass adapter/evidence validation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from hashlib import sha256
from hmac import new as hmac_new
from itertools import islice
from secrets import token_bytes
from typing import Protocol

from customer_signal.domain.models import CustomerEvent, EvidenceRecord, IdentityEdge
from customer_signal.domain.sources import EventScope, SourceManifest
from customer_signal.domain.types import SourceId


class SourceAdapter(Protocol):
    """A bounded provider for one manifest-defined canonical event source."""

    def describe(self) -> SourceManifest: ...

    def load_events(self, scope: EventScope) -> Iterable[CustomerEvent]: ...

    def load_identities(self, scope: EventScope) -> Iterable[IdentityEdge]: ...


class EvidenceProvider(Protocol):
    """Returns authorized evidence records in requested order and masked form."""

    def get_evidence(self, allowed_evidence_ids: Sequence[str]) -> list[EvidenceRecord]: ...


def _edge_key(edge: IdentityEdge) -> tuple[str, str, str, str, str]:
    return (
        edge.left.namespace,
        edge.left.value,
        edge.right.namespace,
        edge.right.value,
        edge.link_type,
    )


def _validate_identity_resolution(
    events: Sequence[CustomerEvent], edges: Sequence[IdentityEdge]
) -> None:
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for edge in edges:
        left = (edge.left.namespace, edge.left.value)
        right = (edge.right.namespace, edge.right.value)
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)

    for event in events:
        if not event.identities:
            raise ValueError("every event must include an identity")
        for identity in event.identities:
            pending = [(identity.namespace, identity.value)]
            visited: set[tuple[str, str]] = set()
            while pending:
                node = pending.pop()
                if node in visited:
                    continue
                visited.add(node)
                pending.extend(graph.get(node, set()) - visited)
            resolved = {value for namespace, value in visited if namespace == "canonical_customer"}
            if resolved != {event.canonical_customer_id}:
                raise ValueError(
                    "event identities must resolve to exactly one canonical_customer_id"
                )


def _identity_closure(
    events: Sequence[CustomerEvent], edges: Iterable[IdentityEdge]
) -> list[IdentityEdge]:
    """Return only the graph component needed by the supplied event identities."""

    materialized_edges = list(edges)
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for edge in materialized_edges:
        left = (edge.left.namespace, edge.left.value)
        right = (edge.right.namespace, edge.right.value)
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)

    connected = {
        (identity.namespace, identity.value) for event in events for identity in event.identities
    }
    pending = list(connected)
    while pending:
        node = pending.pop()
        for neighbor in graph.get(node, set()) - connected:
            connected.add(neighbor)
            pending.append(neighbor)

    return sorted(
        [
            edge
            for edge in materialized_edges
            if (edge.left.namespace, edge.left.value) in connected
            and (edge.right.namespace, edge.right.value) in connected
        ],
        key=_edge_key,
    )


def _validate_loaded_events(
    adapter: SourceAdapter,
    scope: EventScope,
    events: list[CustomerEvent],
) -> None:
    manifest = adapter.describe()
    if scope.source_ids != [manifest.source_id]:
        raise ValueError("adapter contract scope must select exactly its manifest source")
    if len(events) > scope.max_events:
        raise ValueError("adapter returned more events than max_events")
    if events != sorted(events, key=lambda event: (event.occurred_at, event.event_id)):
        raise ValueError("adapter events must be sorted by occurred_at and event_id")
    for event in events:
        if event.source_id != manifest.source_id:
            raise ValueError("adapter returned an event for another source")
        if not scope.start_at <= event.occurred_at < scope.end_at:
            raise ValueError("adapter returned an event outside its half-open scope")
        manifest.validate_event(event)
    _validate_identity_resolution(events, adapter.load_identities(scope))


def validate_adapter_contract(adapter: SourceAdapter, scope: EventScope) -> list[CustomerEvent]:
    """Load once, then validate the exact event response returned by an adapter."""

    events = list(islice(adapter.load_events(scope), scope.max_events + 1))
    _validate_loaded_events(adapter, scope, events)
    return events


class SourceRegistry:
    """Unique dynamic adapter catalog with an owned evidence-provider boundary."""

    def __init__(
        self,
        adapters: Sequence[SourceAdapter],
        evidence: EvidenceProvider,
    ) -> None:
        self._adapters: dict[str, SourceAdapter] = {}
        self._evidence = evidence
        self._customer_masking_key = token_bytes(32)
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        manifest = adapter.describe()
        if manifest.source_id in self._adapters:
            raise ValueError(f"source {manifest.source_id} is already registered")
        self._adapters[manifest.source_id] = adapter

    def get(self, source_id: SourceId) -> SourceAdapter:
        try:
            return self._adapters[source_id]
        except KeyError as error:
            raise LookupError(f"source {source_id} is not registered") from error

    def manifests(self, source_ids: Sequence[SourceId]) -> list[SourceManifest]:
        requested = _validate_requested_source_ids(source_ids)
        return [self.get(source_id).describe() for source_id in requested]

    def load_manifests(self, source_ids: Sequence[SourceId]) -> list[SourceManifest]:
        """Compatibility alias for the original Task 1 registry method name."""

        return self.manifests(source_ids)

    def load_events(self, scope: EventScope) -> list[CustomerEvent]:
        events: list[CustomerEvent] = []
        for source_id in scope.source_ids:
            source_scope = scope.model_copy(update={"source_ids": [source_id]})
            events.extend(validate_adapter_contract(self.get(source_id), source_scope))
        _validate_global_event_identifiers(events)
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return events[: scope.max_events]

    def load_identities(
        self,
        scope: EventScope,
        *,
        authorized_events: Sequence[CustomerEvent] | None = None,
    ) -> list[IdentityEdge]:
        """Resolve only the caller-authorized, globally selected event identities.

        ``authorized_events`` must be the exact list returned by ``load_events`` for
        this run.  Keeping that context in the caller (rather than this registry)
        avoids a cross-run cache and never replays a potentially one-shot event loader.
        """

        events = self._validated_identity_authorized_events(scope, authorized_events)
        events_by_source = {source_id: [] for source_id in scope.source_ids}
        for event in events:
            events_by_source[event.source_id].append(event)

        edges: dict[tuple[str, str, str, str, str], IdentityEdge] = {}
        for source_id in scope.source_ids:
            source_events = events_by_source[source_id]
            if not source_events:
                continue
            source_scope = scope.model_copy(
                update={"source_ids": [source_id], "max_events": len(source_events)}
            )
            for edge in self.get(source_id).load_identities(source_scope):
                edges[_edge_key(edge)] = edge
        closure = _identity_closure(events, edges.values())
        _validate_identity_resolution(events, closure)
        return closure

    def get_evidence(
        self,
        allowed_evidence_ids: Sequence[str],
        *,
        authorized_events: Sequence[CustomerEvent] | None = None,
    ) -> list[EvidenceRecord]:
        """Project evidence from one explicit, caller-owned run authorization context."""

        requested = _validate_evidence_ids(allowed_evidence_ids)
        authorized_by_evidence = self._validated_authorized_events(authorized_events)
        missing = [
            evidence_id for evidence_id in requested if evidence_id not in authorized_by_evidence
        ]
        if missing:
            raise ValueError("evidence is not authorized by the supplied event context")
        records = self._evidence.get_evidence(requested)
        if [record.evidence_id for record in records] != requested:
            raise ValueError("evidence provider must return exactly the requested IDs in order")
        if any(record.raw_fields != {} for record in records):
            raise ValueError("evidence provider must return display-safe masked records")
        for record, evidence_id in zip(records, requested, strict=True):
            event = authorized_by_evidence[evidence_id]
            if (
                record.source_id not in self._adapters
                or record.source_id != event.source_id
                or record.occurred_at != event.occurred_at
            ):
                raise ValueError(
                    "evidence provider record does not match authorized source/customer context"
                )
        return [
            _public_evidence_record(
                authorized_by_evidence[evidence_id],
                masking_key=self._customer_masking_key,
            )
            for evidence_id in requested
        ]

    def _validated_authorized_events(
        self, authorized_events: Sequence[CustomerEvent] | None
    ) -> dict[str, CustomerEvent]:
        if authorized_events is None:
            raise ValueError("authorized event context is required for evidence retrieval")
        if isinstance(authorized_events, (str, bytes)):
            raise ValueError("authorized event context must be a sequence of events")

        authorized_by_evidence: dict[str, CustomerEvent] = {}
        for event in authorized_events:
            try:
                adapter = self.get(event.source_id)
            except LookupError as error:
                raise ValueError("authorized event source is not registered") from error
            adapter.describe().validate_event(event)
            if event.evidence_id in authorized_by_evidence:
                raise ValueError("authorized event context must have unique evidence IDs")
            authorized_by_evidence[event.evidence_id] = event
        return authorized_by_evidence

    def _validated_identity_authorized_events(
        self,
        scope: EventScope,
        authorized_events: Sequence[CustomerEvent] | None,
    ) -> list[CustomerEvent]:
        if authorized_events is None:
            raise ValueError("authorized event context is required for identity retrieval")
        if isinstance(authorized_events, (str, bytes)):
            raise ValueError("authorized event context must be a sequence of events")

        events = list(authorized_events)
        if any(not isinstance(event, CustomerEvent) for event in events):
            raise ValueError("authorized event context must contain canonical events")
        if len(events) > scope.max_events:
            raise ValueError("authorized event context exceeds scope max_events")
        if events != sorted(events, key=lambda event: (event.occurred_at, event.event_id)):
            raise ValueError("authorized events must keep global occurred_at/event_id order")
        _validate_global_event_identifiers(events)

        for event in events:
            if (
                event.source_id not in scope.source_ids
                or not scope.start_at <= event.occurred_at < scope.end_at
            ):
                raise ValueError("authorized event does not match identity scope")
            try:
                adapter = self.get(event.source_id)
            except LookupError as error:
                raise ValueError("authorized event source is not registered") from error
            adapter.describe().validate_event(event)
        return events


def _validate_global_event_identifiers(events: Sequence[CustomerEvent]) -> None:
    event_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for event in events:
        if event.event_id in event_ids:
            raise ValueError("duplicate event_id across registered adapters")
        if event.evidence_id in evidence_ids:
            raise ValueError("duplicate evidence_id across registered adapters")
        event_ids.add(event.event_id)
        evidence_ids.add(event.evidence_id)


def _public_evidence_record(event: CustomerEvent, *, masking_key: bytes) -> EvidenceRecord:
    customer_token = hmac_new(
        masking_key,
        event.canonical_customer_id.encode("utf-8"),
        sha256,
    ).hexdigest()
    return EvidenceRecord(
        evidence_id=event.evidence_id,
        source_id=event.source_id,
        occurred_at=event.occurred_at,
        masked_customer_id=f"customer_{customer_token[:24]}",
        summary=(f"{event.event_type} event: topic={event.topic}; outcome={event.outcome}"),
        raw_fields={},
    )


def _validate_requested_source_ids(source_ids: Sequence[SourceId]) -> list[SourceId]:
    if isinstance(source_ids, (str, bytes)):
        raise ValueError("source_ids must be a non-empty unique sequence")
    values = list(source_ids)
    if not 1 <= len(values) <= 32 or len(values) != len(set(values)):
        raise ValueError("source_ids must be a non-empty unique sequence of at most 32 values")
    return values


def _validate_evidence_ids(evidence_ids: Sequence[str]) -> list[str]:
    if isinstance(evidence_ids, (str, bytes)):
        raise ValueError("allowed_evidence_ids must be a non-empty sequence")
    identifiers = list(evidence_ids)
    if (
        not identifiers
        or len(identifiers) > 100
        or any(not isinstance(identifier, str) or not identifier for identifier in identifiers)
    ):
        raise ValueError("allowed_evidence_ids must contain 1 to 100 non-empty strings")
    return identifiers


__all__ = [
    "EvidenceProvider",
    "SourceAdapter",
    "SourceRegistry",
    "validate_adapter_contract",
]
