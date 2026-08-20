"""Shared contract checks for synthetic and in-memory source adapters."""

from datetime import timedelta

import pytest

from customer_signal.data.source_registry import SourceRegistry, validate_adapter_contract
from customer_signal.domain.models import IdentityEdge, IdentityRef
from customer_signal.domain.sources import EventScope
from customer_signal.synthetic.adapter import SyntheticDuckDBAdapter
from customer_signal.synthetic.manifest import synthetic_source_manifest
from support.in_memory_adapter import InMemorySourceAdapter


def _scope(dataset) -> EventScope:
    return EventScope(
        start_at=dataset.events[0].occurred_at,
        end_at=dataset.events[-1].occurred_at + timedelta(seconds=1),
        source_ids=["search_history"],
        max_events=10_000,
    )


def _memory_adapter(dataset) -> InMemorySourceAdapter:
    return InMemorySourceAdapter(
        synthetic_source_manifest("search_history", dataset.events),
        [event for event in dataset.events if event.source_id == "search_history"],
        dataset.identity_edges,
        [record for record in dataset.evidence if record.source_id == "search_history"],
    )


def _duckdb_adapter(repository, dataset) -> SyntheticDuckDBAdapter:
    return SyntheticDuckDBAdapter(
        repository,
        synthetic_source_manifest("search_history", dataset.events),
    )


def test_in_memory_adapter_obeys_shared_event_identity_contract(synthetic_dataset) -> None:
    validate_adapter_contract(_memory_adapter(synthetic_dataset), _scope(synthetic_dataset))


def test_duckdb_adapter_obeys_shared_event_identity_contract(repository, synthetic_dataset) -> None:
    validate_adapter_contract(
        _duckdb_adapter(repository, synthetic_dataset), _scope(synthetic_dataset)
    )


def test_synthetic_manifests_declare_real_event_namespaces_and_static_cadence(
    synthetic_dataset,
) -> None:
    for source_id in {
        "search_history",
        "search_feedback",
        "digital_behavior",
        "subscription",
        "voc",
    }:
        manifest = synthetic_source_manifest(source_id, synthetic_dataset.events)
        source_events = [
            event for event in synthetic_dataset.events if event.source_id == source_id
        ]

        assert manifest.refresh_cadence == "static_demo"
        assert {identity.namespace for event in source_events for identity in event.identities} == {
            manifest.identity_quality.namespace
        }


@pytest.mark.parametrize("adapter_kind", ["memory", "duckdb"])
def test_registry_returns_only_requested_masked_evidence_in_requested_order(
    adapter_kind: str, repository, synthetic_dataset
) -> None:
    adapter = (
        _memory_adapter(synthetic_dataset)
        if adapter_kind == "memory"
        else _duckdb_adapter(repository, synthetic_dataset)
    )
    registry = SourceRegistry([adapter], evidence=adapter)
    requested = [
        record.evidence_id
        for record in reversed(synthetic_dataset.evidence)
        if record.source_id == "search_history"
    ][:2]

    records = registry.get_evidence(requested)

    assert [record.evidence_id for record in records] == requested
    assert all(record.source_id == "search_history" for record in records)
    assert all(record.raw_fields == {} for record in records)
    unapproved_id = next(
        record.evidence_id
        for record in synthetic_dataset.evidence
        if record.source_id != "search_history"
    )
    with pytest.raises(ValueError, match="evidence does not belong"):
        registry.get_evidence([unapproved_id])


def test_adapter_contract_rejects_identity_with_no_canonical_resolution(synthetic_dataset) -> None:
    scope = _scope(synthetic_dataset)
    event = next(event for event in synthetic_dataset.events if event.source_id == "search_history")
    invalid_event = event.model_copy(
        update={"identities": [IdentityRef(namespace="search_run", value="orphan")]}
    )
    adapter = InMemorySourceAdapter(
        synthetic_source_manifest("search_history", synthetic_dataset.events),
        [invalid_event],
        synthetic_dataset.identity_edges,
        [],
    )

    with pytest.raises(ValueError, match="exactly one canonical_customer_id"):
        validate_adapter_contract(adapter, scope)


def test_adapter_contract_rejects_identity_with_multiple_canonical_resolutions(
    synthetic_dataset,
) -> None:
    scope = _scope(synthetic_dataset)
    event = next(event for event in synthetic_dataset.events if event.source_id == "search_history")
    duplicate_canonical_edge = IdentityEdge(
        left=event.identities[0],
        right=IdentityRef(namespace="canonical_customer", value="CUST-002"),
        link_type="SYNTHETIC",
        confidence=1.0,
        provenance="deliberately ambiguous test graph",
    )
    adapter = InMemorySourceAdapter(
        synthetic_source_manifest("search_history", synthetic_dataset.events),
        [event],
        [*synthetic_dataset.identity_edges, duplicate_canonical_edge],
        [],
    )

    with pytest.raises(ValueError, match="exactly one canonical_customer_id"):
        validate_adapter_contract(adapter, scope)


def test_registry_loads_each_adapter_once_and_returns_the_validated_response(
    synthetic_dataset,
) -> None:
    class StatefulAdapter:
        def __init__(self) -> None:
            self.load_calls = 0
            self._manifest = synthetic_source_manifest("search_history", synthetic_dataset.events)
            self._event = next(
                event for event in synthetic_dataset.events if event.source_id == "search_history"
            )

        def describe(self):
            return self._manifest

        def load_events(self, scope):
            del scope
            self.load_calls += 1
            if self.load_calls > 1:
                raise AssertionError("a second unvalidated response was requested")
            return [self._event]

        def load_identities(self, scope):
            del scope
            return synthetic_dataset.identity_edges

        def get_evidence(self, allowed_evidence_ids):
            del allowed_evidence_ids
            return []

    adapter = StatefulAdapter()
    registry = SourceRegistry([adapter], evidence=adapter)
    scope = _scope(synthetic_dataset)

    assert registry.load_events(scope) == [adapter._event]
    assert adapter.load_calls == 1


def test_registry_validates_a_one_shot_event_iterable_once(synthetic_dataset) -> None:
    class OneShotAdapter:
        def __init__(self) -> None:
            self._manifest = synthetic_source_manifest("search_history", synthetic_dataset.events)
            self._event = next(
                event for event in synthetic_dataset.events if event.source_id == "search_history"
            )

        def describe(self):
            return self._manifest

        def load_events(self, scope):
            del scope
            yield self._event

        def load_identities(self, scope):
            del scope
            return synthetic_dataset.identity_edges

        def get_evidence(self, allowed_evidence_ids):
            del allowed_evidence_ids
            return []

    adapter = OneShotAdapter()
    registry = SourceRegistry([adapter], evidence=adapter)

    assert registry.load_events(_scope(synthetic_dataset)) == [adapter._event]


def test_registry_preserves_requested_source_order_and_validates_registered_events(
    repository, synthetic_dataset
) -> None:
    adapters = [
        SyntheticDuckDBAdapter(
            repository,
            synthetic_source_manifest(source_id, synthetic_dataset.events),
        )
        for source_id in ("voc", "search_history")
    ]
    registry = SourceRegistry(adapters, evidence=adapters[0])
    scope = EventScope(
        start_at=synthetic_dataset.events[0].occurred_at,
        end_at=synthetic_dataset.events[-1].occurred_at + timedelta(seconds=1),
        source_ids=["voc", "search_history"],
        max_events=10_000,
    )

    assert [manifest.source_id for manifest in registry.load_manifests(scope.source_ids)] == [
        "voc",
        "search_history",
    ]
    events = registry.load_events(scope)
    assert events == sorted(events, key=lambda item: (item.occurred_at, item.event_id))
    assert {event.source_id for event in events} == {"voc", "search_history"}

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            SyntheticDuckDBAdapter(
                repository,
                synthetic_source_manifest("voc", synthetic_dataset.events),
            )
        )
