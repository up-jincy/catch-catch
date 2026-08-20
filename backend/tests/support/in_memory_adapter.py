"""Small in-memory implementation used by shared source-adapter contract tests."""

from collections.abc import Sequence

from customer_signal.domain.models import CustomerEvent, EvidenceRecord, IdentityEdge
from customer_signal.domain.sources import EventScope, SourceManifest


class InMemorySourceAdapter:
    def __init__(
        self,
        manifest: SourceManifest,
        events: list[CustomerEvent],
        identity_edges: list[IdentityEdge],
        evidence: list[EvidenceRecord],
    ) -> None:
        self._manifest = manifest
        self._events = list(events)
        self._identity_edges = list(identity_edges)
        self._evidence = {record.evidence_id: record for record in evidence}

    def describe(self) -> SourceManifest:
        return self._manifest

    def load_events(self, scope: EventScope) -> list[CustomerEvent]:
        return self._selected_events(scope)

    def load_identities(self, scope: EventScope) -> list[IdentityEdge]:
        graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for edge in self._identity_edges:
            left = (edge.left.namespace, edge.left.value)
            right = (edge.right.namespace, edge.right.value)
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)

        connected = {
            (identity.namespace, identity.value)
            for event in self._selected_events(scope)
            for identity in event.identities
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
                for edge in self._identity_edges
                if (edge.left.namespace, edge.left.value) in connected
                and (edge.right.namespace, edge.right.value) in connected
            ],
            key=lambda edge: (
                edge.left.namespace,
                edge.left.value,
                edge.right.namespace,
                edge.right.value,
                edge.link_type,
            ),
        )

    def _selected_events(self, scope: EventScope) -> list[CustomerEvent]:
        return [
            event
            for event in sorted(self._events, key=lambda item: (item.occurred_at, item.event_id))
            if event.source_id in scope.source_ids
            and scope.start_at <= event.occurred_at < scope.end_at
        ][: scope.max_events]

    def get_evidence(self, allowed_evidence_ids: Sequence[str]) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        for evidence_id in allowed_evidence_ids:
            try:
                record = self._evidence[evidence_id]
            except KeyError as error:
                raise ValueError("evidence does not belong to this source") from error
            if record.source_id != self._manifest.source_id:
                raise ValueError("evidence does not belong to this source")
            records.append(record.model_copy(update={"raw_fields": {}}))
        return records
