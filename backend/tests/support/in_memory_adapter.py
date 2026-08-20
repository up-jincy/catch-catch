"""Small in-memory implementation used by shared source-adapter contract tests."""

from customer_signal.domain.models import CustomerEvent, IdentityEdge
from customer_signal.domain.sources import EventScope, SourceManifest


class InMemorySourceAdapter:
    def __init__(
        self,
        manifest: SourceManifest,
        events: list[CustomerEvent],
        identity_edges: list[IdentityEdge],
    ) -> None:
        self._manifest = manifest
        self._events = list(events)
        self._identity_edges = list(identity_edges)

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
