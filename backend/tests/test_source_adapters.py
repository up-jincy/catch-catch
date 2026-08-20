"""Shared contract checks for synthetic and in-memory source adapters."""

from datetime import timedelta

import pytest

from customer_signal.data.source_registry import SourceRegistry, validate_adapter_contract
from customer_signal.domain.models import IdentityEdge, IdentityRef
from customer_signal.domain.sources import EventScope, MeasureDescriptor
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


def _source_adapter(adapter_kind: str, source_id: str, repository, dataset):
    manifest = synthetic_source_manifest(source_id, dataset.events)
    if adapter_kind == "memory":
        return InMemorySourceAdapter(
            manifest,
            [event for event in dataset.events if event.source_id == source_id],
            dataset.identity_edges,
            [record for record in dataset.evidence if record.source_id == source_id],
        )
    return SyntheticDuckDBAdapter(repository, manifest)


def _edge_key(edge: IdentityEdge) -> tuple[str, str, str, str, str]:
    return (
        edge.left.namespace,
        edge.left.value,
        edge.right.namespace,
        edge.right.value,
        edge.link_type,
    )


def _connected_edges(events, edges: list[IdentityEdge]) -> list[IdentityEdge]:
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for edge in edges:
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
            for edge in edges
            if (edge.left.namespace, edge.left.value) in connected
            and (edge.right.namespace, edge.right.value) in connected
        ],
        key=_edge_key,
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


@pytest.mark.parametrize(
    ("source_id", "dimensions", "measures"),
    [
        ("search_history", {"customer_ref", "is_repeat"}, {"result_count"}),
        ("search_feedback", {"customer_ref"}, {"rating"}),
        ("digital_behavior", {"customer_ref", "authenticated"}, {"session_depth"}),
        (
            "subscription",
            {"customer_ref", "product_family", "stage", "status"},
            set(),
        ),
        ("voc", {"customer_ref", "contact_channel", "noise"}, set()),
    ],
)
def test_synthetic_manifests_declare_normalized_event_fields(
    source_id: str,
    dimensions: set[str],
    measures: set[str],
    synthetic_dataset,
) -> None:
    manifest = synthetic_source_manifest(source_id, synthetic_dataset.events)

    assert set(manifest.dimensions) == dimensions
    assert set(manifest.measures) == measures


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
    authorized_events = registry.load_events(_scope(synthetic_dataset))
    requested = [
        record.evidence_id
        for record in reversed(synthetic_dataset.evidence)
        if record.source_id == "search_history"
    ][:2]

    records = registry.get_evidence(requested, authorized_events=authorized_events)

    assert [record.evidence_id for record in records] == requested
    assert all(record.source_id == "search_history" for record in records)
    assert all(record.raw_fields == {} for record in records)
    unapproved_id = next(
        record.evidence_id
        for record in synthetic_dataset.evidence
        if record.source_id != "search_history"
    )
    with pytest.raises(ValueError, match="not authorized"):
        registry.get_evidence([unapproved_id], authorized_events=authorized_events)


def test_registry_requires_explicit_per_call_evidence_authorization(synthetic_dataset) -> None:
    adapter = _memory_adapter(synthetic_dataset)
    registry = SourceRegistry([adapter], evidence=adapter)
    evidence_id = next(
        record.evidence_id
        for record in synthetic_dataset.evidence
        if record.source_id == "search_history"
    )

    with pytest.raises(ValueError, match="authorized event"):
        registry.get_evidence([evidence_id])


def test_registry_requires_explicit_per_call_identity_authorization(synthetic_dataset) -> None:
    adapter = _memory_adapter(synthetic_dataset)
    registry = SourceRegistry([adapter], evidence=adapter)

    with pytest.raises(ValueError, match="authorized event"):
        registry.load_identities(_scope(synthetic_dataset))


def test_registry_projects_evidence_only_from_authorized_event_semantics(synthetic_dataset) -> None:
    adapter = _memory_adapter(synthetic_dataset)
    selected_event = adapter.load_events(_scope(synthetic_dataset))[0].model_copy(
        update={
            "text": "RAW SECRET SSN 123-45-6789",
            "attributes": {"ssn": "123-45-6789"},
        }
    )
    provider_record = next(
        record
        for record in synthetic_dataset.evidence
        if record.evidence_id == selected_event.evidence_id
    ).model_copy(
        update={
            "summary": "Provider SSN 987-65-4321",
            "masked_customer_id": "CUST-999",
            "raw_fields": {},
        }
    )

    class MaliciousProvider:
        def get_evidence(self, allowed_evidence_ids):
            assert allowed_evidence_ids == [selected_event.evidence_id]
            return [provider_record]

    registry = SourceRegistry([adapter], evidence=MaliciousProvider())
    records = registry.get_evidence(
        [selected_event.evidence_id], authorized_events=[selected_event]
    )
    repeated = registry.get_evidence(
        [selected_event.evidence_id], authorized_events=[selected_event]
    )
    payload = records[0].model_dump_json()

    assert records[0].evidence_id == selected_event.evidence_id
    assert records[0].source_id == selected_event.source_id
    assert records[0].occurred_at == selected_event.occurred_at
    assert records[0].raw_fields == {}
    assert records[0].summary != provider_record.summary
    assert records[0].masked_customer_id != provider_record.masked_customer_id
    assert records[0].masked_customer_id == repeated[0].masked_customer_id
    for forbidden in (
        "RAW SECRET",
        "Provider SSN",
        "123-45-6789",
        "987-65-4321",
        selected_event.canonical_customer_id,
    ):
        assert forbidden not in payload


def test_registry_rejects_evidence_provider_record_from_unregistered_source(
    synthetic_dataset,
) -> None:
    adapter = _memory_adapter(synthetic_dataset)
    selected_event = adapter.load_events(_scope(synthetic_dataset))[0]
    provider_record = next(
        record
        for record in synthetic_dataset.evidence
        if record.evidence_id == selected_event.evidence_id
    ).model_copy(update={"source_id": "external_partner", "raw_fields": {}})

    class MaliciousProvider:
        def get_evidence(self, allowed_evidence_ids):
            assert allowed_evidence_ids == [selected_event.evidence_id]
            return [provider_record]

    registry = SourceRegistry([adapter], evidence=MaliciousProvider())

    with pytest.raises(ValueError, match="authorized source/customer context"):
        registry.get_evidence([selected_event.evidence_id], authorized_events=[selected_event])


def test_registry_rejects_evidence_provider_record_from_another_customer(synthetic_dataset) -> None:
    adapter = _memory_adapter(synthetic_dataset)
    events = adapter.load_events(_scope(synthetic_dataset))
    selected_event = events[0]
    other_event = next(
        event
        for event in events
        if event.canonical_customer_id != selected_event.canonical_customer_id
    )
    provider_record = next(
        record
        for record in synthetic_dataset.evidence
        if record.evidence_id == other_event.evidence_id
    ).model_copy(update={"evidence_id": selected_event.evidence_id, "raw_fields": {}})

    class MaliciousProvider:
        def get_evidence(self, allowed_evidence_ids):
            assert allowed_evidence_ids == [selected_event.evidence_id]
            return [provider_record]

    registry = SourceRegistry([adapter], evidence=MaliciousProvider())

    with pytest.raises(ValueError, match="authorized source/customer context"):
        registry.get_evidence([selected_event.evidence_id], authorized_events=[selected_event])


@pytest.mark.parametrize(
    ("semantic_type", "value"),
    [
        ("number", True),
        ("number", "12.5"),
        ("number", float("nan")),
        ("number", float("inf")),
        ("number", float("-inf")),
        ("integer", True),
        ("integer", "12"),
        ("integer", float("nan")),
        ("integer", float("inf")),
        ("integer", float("-inf")),
        ("integer", 12.5),
    ],
)
def test_registry_rejects_mutated_invalid_measure_before_returning_events(
    semantic_type: str, value: object, synthetic_dataset
) -> None:
    manifest = synthetic_source_manifest("search_history", synthetic_dataset.events).model_copy(
        update={
            "measures": {
                "amount": MeasureDescriptor(
                    semantic_type=semantic_type,
                    description="Mutable test measure",
                    unit="count",
                )
            }
        }
    )
    event = next(
        event for event in synthetic_dataset.events if event.source_id == "search_history"
    ).model_copy(update={"measures": {"amount": value}})
    adapter = InMemorySourceAdapter(
        manifest,
        [event],
        synthetic_dataset.identity_edges,
        [],
    )
    registry = SourceRegistry([adapter], evidence=adapter)

    with pytest.raises(ValueError, match="measure"):
        registry.load_events(_scope(synthetic_dataset))


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

    events = registry.load_events(scope)

    assert events == [adapter._event]
    assert registry.load_identities(scope, authorized_events=events) == _connected_edges(
        events, synthetic_dataset.identity_edges
    )
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


def test_registry_stops_consuming_a_long_event_generator_after_the_overflow_probe(
    synthetic_dataset,
) -> None:
    class LongGeneratorAdapter:
        def __init__(self) -> None:
            self.consumed = 0
            self._manifest = synthetic_source_manifest("search_history", synthetic_dataset.events)
            self._event = next(
                event for event in synthetic_dataset.events if event.source_id == "search_history"
            )

        def describe(self):
            return self._manifest

        def load_events(self, scope):
            del scope
            for index in range(5_000):
                self.consumed += 1
                yield self._event.model_copy(
                    update={
                        "event_id": f"{self._event.event_id}-{index}",
                        "evidence_id": f"{self._event.evidence_id}-{index}",
                        "occurred_at": self._event.occurred_at + timedelta(microseconds=index),
                    }
                )

        def load_identities(self, scope):
            del scope
            return synthetic_dataset.identity_edges

    adapter = LongGeneratorAdapter()
    registry = SourceRegistry([adapter], evidence=adapter)
    scope = _scope(synthetic_dataset).model_copy(update={"max_events": 3})

    with pytest.raises(ValueError, match="more events than max_events"):
        registry.load_events(scope)

    assert adapter.consumed == scope.max_events + 1


@pytest.mark.parametrize("adapter_kind", ["memory", "duckdb"])
def test_adapters_return_only_deterministic_identity_edges_for_selected_events(
    adapter_kind: str, repository, synthetic_dataset
) -> None:
    adapter = (
        _memory_adapter(synthetic_dataset)
        if adapter_kind == "memory"
        else _duckdb_adapter(repository, synthetic_dataset)
    )
    scope = _scope(synthetic_dataset).model_copy(update={"max_events": 1})
    events = adapter.load_events(scope)
    edges = adapter.load_identities(scope)

    assert len(events) == 1
    assert edges == _connected_edges(events, synthetic_dataset.identity_edges)
    assert [_edge_key(edge) for edge in edges] == sorted(_edge_key(edge) for edge in edges)


@pytest.mark.parametrize("adapter_kind", ["memory", "duckdb"])
def test_registry_identities_follow_exact_globally_limited_authorized_events(
    adapter_kind: str, repository, synthetic_dataset
) -> None:
    source_ids = ["search_history", "digital_behavior"]
    adapters = [
        _source_adapter(adapter_kind, source_id, repository, synthetic_dataset)
        for source_id in source_ids
    ]
    registry = SourceRegistry(adapters, evidence=adapters[0])
    scope = EventScope(
        start_at=synthetic_dataset.events[0].occurred_at,
        end_at=synthetic_dataset.events[-1].occurred_at + timedelta(seconds=1),
        source_ids=source_ids,
        max_events=1,
    )

    events = registry.load_events(scope)
    edges = registry.load_identities(scope, authorized_events=events)
    expected_edges = _connected_edges(events, synthetic_dataset.identity_edges)

    assert [event.canonical_customer_id for event in events] == ["CUST-015"]
    assert edges == expected_edges
    assert {
        value
        for edge in edges
        for namespace, value in (
            (edge.left.namespace, edge.left.value),
            (edge.right.namespace, edge.right.value),
        )
        if namespace == "canonical_customer"
    } == {"CUST-015"}
    assert all("CUST-003" not in (edge.left.value, edge.right.value) for edge in edges)


def test_registry_rejects_identity_authorization_events_outside_scope(
    repository, synthetic_dataset
) -> None:
    source_ids = ["search_history", "digital_behavior"]
    adapters = [
        _source_adapter("duckdb", source_id, repository, synthetic_dataset)
        for source_id in source_ids
    ]
    registry = SourceRegistry(adapters, evidence=adapters[0])
    scope = _scope(synthetic_dataset)
    outside_source = next(
        event for event in synthetic_dataset.events if event.source_id == "digital_behavior"
    )
    outside_time = next(
        event for event in synthetic_dataset.events if event.source_id == "search_history"
    ).model_copy(update={"occurred_at": scope.end_at})

    for event in (outside_source, outside_time):
        with pytest.raises(ValueError, match="does not match identity scope"):
            registry.load_identities(scope, authorized_events=[event])


@pytest.mark.parametrize(
    ("field_name", "message"),
    [("event_id", "duplicate event_id"), ("evidence_id", "duplicate evidence_id")],
)
def test_registry_rejects_duplicate_global_identifiers_in_identity_authorization(
    field_name: str, message: str, synthetic_dataset
) -> None:
    adapter = _memory_adapter(synthetic_dataset)
    registry = SourceRegistry([adapter], evidence=adapter)
    event = adapter.load_events(_scope(synthetic_dataset))[0]
    duplicate = event.model_copy(
        update={
            "event_id": event.event_id if field_name == "event_id" else f"{event.event_id}-other",
            "evidence_id": event.evidence_id
            if field_name == "evidence_id"
            else f"{event.evidence_id}-other",
        }
    )

    with pytest.raises(ValueError, match=message):
        registry.load_identities(_scope(synthetic_dataset), authorized_events=[event, duplicate])


@pytest.mark.parametrize(
    ("collision_field", "message"),
    [
        ("event_id", "duplicate event_id"),
        ("evidence_id", "duplicate evidence_id"),
    ],
)
def test_registry_rejects_identifier_collisions_across_registered_adapters(
    collision_field: str, message: str, synthetic_dataset
) -> None:
    first_event = next(
        event for event in synthetic_dataset.events if event.source_id == "search_history"
    )
    second_event = next(
        event for event in synthetic_dataset.events if event.source_id == "search_feedback"
    ).model_copy(update={collision_field: getattr(first_event, collision_field)})
    adapters = [
        InMemorySourceAdapter(
            synthetic_source_manifest("search_history", synthetic_dataset.events),
            [first_event],
            synthetic_dataset.identity_edges,
            [],
        ),
        InMemorySourceAdapter(
            synthetic_source_manifest("search_feedback", synthetic_dataset.events),
            [second_event],
            synthetic_dataset.identity_edges,
            [],
        ),
    ]
    registry = SourceRegistry(adapters, evidence=adapters[0])
    scope = _scope(synthetic_dataset).model_copy(
        update={"source_ids": ["search_history", "search_feedback"], "max_events": 2}
    )

    with pytest.raises(ValueError, match=message):
        registry.load_events(scope)


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
