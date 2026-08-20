"""Dynamic source-adapter registration and shared adapter contract checks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from customer_signal.domain.models import CustomerEvent, EvidenceRecord, IdentityEdge
from customer_signal.domain.sources import EventScope, SourceManifest
from customer_signal.domain.types import SourceId


class SourceAdapter(Protocol):
    """A bounded, source-specific provider of canonical events and identities."""

    def describe(self) -> SourceManifest: ...

    def load_events(self, scope: EventScope) -> list[CustomerEvent]: ...

    def load_identities(self, scope: EventScope) -> list[IdentityEdge]: ...


class EvidenceProvider(Protocol):
    """Returns only caller-authorized, display-safe masked evidence records."""

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
            resolved = {
                value for namespace, value in visited if namespace == "canonical_customer"
            }
            if resolved != {event.canonical_customer_id}:
                raise ValueError(
                    "event identities must resolve to exactly one canonical_customer_id"
                )


def validate_adapter_contract(adapter: SourceAdapter, scope: EventScope) -> None:
    """Validate a single adapter's bounded event and identity guarantees."""

    manifest = adapter.describe()
    if set(scope.source_ids) != {manifest.source_id}:
        raise ValueError("adapter contract scope must select exactly its manifest source")
    events = adapter.load_events(scope)
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


class SourceRegistry:
    """Dynamic adapter catalog with source/order/bounds validation at one boundary."""

    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

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

    def load_manifests(self, source_ids: Sequence[SourceId]) -> list[SourceManifest]:
        scoped_source_ids = _validate_requested_source_ids(source_ids)
        return [self.get(source_id).describe() for source_id in scoped_source_ids]

    def load_events(self, scope: EventScope) -> list[CustomerEvent]:
        events: list[CustomerEvent] = []
        for source_id in scope.source_ids:
            source_scope = scope.model_copy(update={"source_ids": [source_id]})
            adapter = self.get(source_id)
            validate_adapter_contract(adapter, source_scope)
            events.extend(adapter.load_events(source_scope))
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return events[: scope.max_events]

    def load_identities(self, scope: EventScope) -> list[IdentityEdge]:
        edges: dict[tuple[str, str, str, str, str], IdentityEdge] = {}
        for source_id in scope.source_ids:
            source_scope = scope.model_copy(update={"source_ids": [source_id]})
            for edge in self.get(source_id).load_identities(source_scope):
                edges[_edge_key(edge)] = edge
        return list(edges.values())


def _validate_requested_source_ids(source_ids: Sequence[SourceId]) -> list[SourceId]:
    if isinstance(source_ids, (str, bytes)):
        raise ValueError("source_ids must be a non-empty unique sequence")
    values = list(source_ids)
    if not 1 <= len(values) <= 32 or len(values) != len(set(values)):
        raise ValueError("source_ids must be a non-empty unique sequence of at most 32 values")
    return values


__all__ = [
    "EvidenceProvider",
    "SourceAdapter",
    "SourceRegistry",
    "validate_adapter_contract",
]
