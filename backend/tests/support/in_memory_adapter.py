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
        return [
            event
            for event in sorted(self._events, key=lambda item: (item.occurred_at, item.event_id))
            if event.source_id in scope.source_ids
            and scope.start_at <= event.occurred_at < scope.end_at
        ][: scope.max_events]

    def load_identities(self, scope: EventScope) -> list[IdentityEdge]:
        del scope
        return list(self._identity_edges)

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
