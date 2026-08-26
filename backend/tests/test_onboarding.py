"""Onboarding pipeline contracts: spec validation, mapped adapter, registry integration."""

from pathlib import Path

import pytest

from customer_signal.data.source_registry import SourceRegistry, validate_adapter_contract
from customer_signal.domain.sources import EventScope
from customer_signal.onboarding.adapter import (
    CompositeEvidenceProvider,
    MappedTableAdapter,
    MappingError,
    load_onboarded_adapters,
)
from customer_signal.onboarding.draft import heuristic_spec
from customer_signal.onboarding.profiler import MAX_ROWS, profile_table
from customer_signal.onboarding.spec import FieldRule, SourceMappingSpec

FIXTURE = Path(__file__).parent / "fixtures" / "payments.csv"
LEGACY_EVENT_TYPES = {"search", "feedback", "digital_behavior", "subscription", "voc"}


def _draft_spec() -> SourceMappingSpec:
    return heuristic_spec(
        profile_table(FIXTURE),
        source_id="payment",
        label="Payments",
        description="Payment attempts from the billing table.",
    )


def _full_scope(adapter: MappedTableAdapter) -> EventScope:
    interval = adapter.describe().data_interval
    return EventScope(
        source_ids=[adapter.describe().source_id],
        start_at=interval.start_at,
        end_at=interval.end_at,
        max_events=MAX_ROWS,
    )


def test_field_rule_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError):
        FieldRule()
    with pytest.raises(ValueError):
        FieldRule(column="status", const="paid")
    with pytest.raises(ValueError):
        FieldRule(const="paid", value_map={"S": "success"})


def test_heuristic_draft_registers_a_new_event_type() -> None:
    spec = _draft_spec()
    adapter = MappedTableAdapter.from_file(spec, FIXTURE)
    events = validate_adapter_contract(adapter, _full_scope(adapter))

    assert len(events) == 8
    assert {event.event_type for event in events} == {"payment"}
    assert "payment" not in LEGACY_EVENT_TYPES
    assert all(event.canonical_customer_id.startswith("CU-") for event in events)
    manifest = adapter.describe()
    assert "email" in manifest.masking_policy.rules


def test_value_map_covers_outcomes_or_fails() -> None:
    spec = _draft_spec().model_copy(
        update={"outcome": FieldRule(column="status", value_map={"S": "success"})}
    )
    with pytest.raises(MappingError, match="value_map"):
        MappedTableAdapter.from_file(spec, FIXTURE)

    complete = _draft_spec().model_copy(
        update={"outcome": FieldRule(column="status", value_map={"S": "success", "F": "failed"})}
    )
    adapter = MappedTableAdapter.from_file(complete, FIXTURE)
    outcomes = {event.outcome for event in validate_adapter_contract(adapter, _full_scope(adapter))}
    assert outcomes == {"success", "failed"}


def test_registry_serves_mapped_events_and_evidence() -> None:
    adapter = MappedTableAdapter.from_file(_draft_spec(), FIXTURE)
    registry = SourceRegistry(
        [adapter],
        evidence=CompositeEvidenceProvider(base=None, adapters=[adapter]),
    )
    scope = _full_scope(adapter)
    events = registry.load_events(scope)
    assert registry.load_identities(scope, authorized_events=events)
    records = registry.get_evidence(
        [events[0].evidence_id],
        authorized_events=events,
    )
    assert records[0].raw_fields == {}
    assert records[0].source_id == "payment"


def test_load_onboarded_adapters_skips_drafts(tmp_path: Path) -> None:
    spec = _draft_spec()
    draft_dir = tmp_path / spec.source_id
    draft_dir.mkdir()
    (draft_dir / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
    (draft_dir / "data.csv").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_onboarded_adapters(tmp_path) == []

    approved = spec.model_copy(update={"status": "approved"})
    (draft_dir / "spec.json").write_text(approved.model_dump_json(), encoding="utf-8")
    adapters = load_onboarded_adapters(tmp_path)
    assert [adapter.describe().source_id for adapter in adapters] == ["payment"]
