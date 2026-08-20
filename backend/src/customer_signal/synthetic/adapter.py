"""DuckDB-backed adapter for one manifest-defined synthetic source."""

from __future__ import annotations

from collections.abc import Sequence

from customer_signal.data.repository import DuckDBRepository
from customer_signal.domain.models import CustomerEvent, EvidenceRecord, IdentityEdge
from customer_signal.domain.sources import EventScope, SourceManifest


class SyntheticDuckDBAdapter:
    """Expose one synthetic DuckDB source through the portable adapter contract."""

    def __init__(self, repository: DuckDBRepository, manifest: SourceManifest) -> None:
        self._repository = repository
        self._manifest = manifest

    def describe(self) -> SourceManifest:
        return self._manifest

    def load_events(self, scope: EventScope) -> list[CustomerEvent]:
        self._validate_scope(scope)
        events = self._repository.list_events(
            scope.start_at,
            scope.end_at,
            [self._manifest.source_id],
            limit=min(scope.max_events, 100),
        )
        for event in events:
            self._manifest.validate_event(event)
        return events

    def load_identities(self, scope: EventScope) -> list[IdentityEdge]:
        self._validate_scope(scope)
        return self._repository.list_identity_edges(
            scope.start_at, scope.end_at, [self._manifest.source_id]
        )

    def get_evidence(self, allowed_evidence_ids: Sequence[str]) -> list[EvidenceRecord]:
        records = self._repository.get_evidence(allowed_evidence_ids)
        if any(record.source_id != self._manifest.source_id for record in records):
            raise ValueError("evidence does not belong to this source")
        return [record.model_copy(update={"raw_fields": {}}) for record in records]

    def _validate_scope(self, scope: EventScope) -> None:
        if scope.source_ids != [self._manifest.source_id]:
            raise ValueError("synthetic adapter scope must select its source only")


__all__ = ["SyntheticDuckDBAdapter"]
