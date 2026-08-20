"""Shared contract checks for synthetic and in-memory source adapters."""

from datetime import timedelta

import pytest

from customer_signal.data.source_registry import SourceRegistry, validate_adapter_contract
from customer_signal.domain.models import IdentityRef
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


def test_in_memory_adapter_obeys_shared_event_identity_contract(synthetic_dataset) -> None:
    scope = _scope(synthetic_dataset)
    adapter = InMemorySourceAdapter(
        synthetic_source_manifest("search_history", synthetic_dataset.events),
        [event for event in synthetic_dataset.events if event.source_id == "search_history"],
        synthetic_dataset.identity_edges,
    )
    validate_adapter_contract(adapter, scope)


def test_duckdb_adapter_obeys_shared_event_identity_contract(repository, synthetic_dataset) -> None:
    scope = _scope(synthetic_dataset)
    adapter = SyntheticDuckDBAdapter(
        repository,
        synthetic_source_manifest("search_history", synthetic_dataset.events),
    )
    validate_adapter_contract(adapter, scope)


def test_adapter_contract_requires_every_identity_to_resolve(synthetic_dataset) -> None:
    scope = _scope(synthetic_dataset)
    event = next(
        event for event in synthetic_dataset.events if event.source_id == "search_history"
    )
    invalid_event = event.model_copy(
        update={
            "identities": [
                *event.identities,
                IdentityRef(namespace="unresolved", value="orphan"),
            ]
        }
    )
    adapter = InMemorySourceAdapter(
        synthetic_source_manifest("search_history", synthetic_dataset.events),
        [invalid_event],
        synthetic_dataset.identity_edges,
    )

    with pytest.raises(ValueError, match="exactly one canonical_customer_id"):
        validate_adapter_contract(adapter, scope)


def test_registry_preserves_requested_source_order_and_validates_registered_events(
    repository, synthetic_dataset
) -> None:
    registry = SourceRegistry()
    for source_id in ("voc", "search_history"):
        registry.register(
            SyntheticDuckDBAdapter(
                repository,
                synthetic_source_manifest(source_id, synthetic_dataset.events),
            )
        )
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
