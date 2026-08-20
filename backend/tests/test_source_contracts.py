"""Contract tests for portable source and primitive domain models."""

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from customer_signal.agent.contracts import RunRequest
from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    PRIMITIVE_INPUT_ADAPTER,
)
from customer_signal.domain.sources import (
    FieldDescriptor,
    IdentityQuality,
    MaskingPolicy,
    PublicSourceManifest,
    RefreshDescriptor,
    SourceManifest,
    TimeRange,
)
from customer_signal.domain.types import GenericPrimitiveName, SourceId


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_source_id_is_dynamic_but_pattern_bounded() -> None:
    source_id = TypeAdapter(SourceId).validate_python("partner_crm")
    assert source_id == "partner_crm"
    request = RunRequest(
        question="What happened?",
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
        enabled_sources=["partner_crm"],
    )
    assert request.enabled_sources == ["partner_crm"]

    with pytest.raises(ValidationError):
        TypeAdapter(SourceId).validate_python("Partner CRM")


def test_generic_primitives_have_exact_nonlegacy_surface() -> None:
    assert set(get_args(GenericPrimitiveName.__value__)) == {
        "catalog_sources",
        "profile_events",
        "aggregate_events",
        "segment_customers",
        "detect_repetition",
        "match_sequence",
        "compare_segments",
        "rank_customers",
        "get_customer_journey",
        "get_evidence",
    }


def test_customer_event_accepts_strict_dimensions_and_finite_measures() -> None:
    event = CustomerEvent(
        event_id="evt-1",
        evidence_id="ev-1",
        source_id="partner_crm",
        occurred_at=NOW,
        event_type="search",
        action="view",
        topic="pricing",
        outcome="none",
        text="Viewed pricing",
        canonical_customer_id="cus-1",
        dimensions={"channel": "web"},
        measures={"amount": 12.5},
    )
    assert event.dimensions == {"channel": "web"}
    assert event.measures == {"amount": 12.5}

    with pytest.raises(ValidationError):
        CustomerEvent(
            **event.model_dump(exclude={"measures"}), measures={"amount": float("inf")}
        )


def test_manifest_rejects_undeclared_event_semantics_and_private_public_fields() -> None:
    manifest = SourceManifest(
        source_id="partner_crm",
        label="Partner CRM",
        description="Partner-provided CRM activity.",
        adapter_version="1.0",
        manifest_version="1.0",
        data_interval=TimeRange(start_at=NOW, end_at=NOW + timedelta(days=1)),
        refresh_cadence=RefreshDescriptor(cadence="daily"),
        supported_event_types=["search"],
        supported_topics=["pricing"],
        supported_outcomes=["none"],
        dimensions=[
            FieldDescriptor(
                name="channel",
                semantic_type="channel",
                description="Interaction channel",
                pii_classification="none",
            ),
            FieldDescriptor(
                name="email",
                semantic_type="email",
                description="Raw address",
                pii_classification="direct",
            ),
        ],
        measures=[],
        generic_capabilities=["aggregate_events"],
        masking_policy=MaskingPolicy(field_masks={"email": "hash"}),
        identity_quality=IdentityQuality(
            level="declared",
            description="Partner account mapping",
            namespace="partner_user",
        ),
    )
    event = CustomerEvent(
        event_id="evt-1",
        evidence_id="ev-1",
        source_id="partner_crm",
        occurred_at=NOW,
        event_type="search",
        action="view",
        topic="pricing",
        outcome="none",
        text="Viewed pricing",
        canonical_customer_id="cus-1",
        dimensions={"channel": "web"},
    )
    manifest.validate_event(event)
    with pytest.raises(ValueError, match="dimension"):
        manifest.validate_event(event.model_copy(update={"dimensions": {"region": "KR"}}))

    public = PublicSourceManifest.from_manifest(manifest)
    assert set(public.model_dump()) == {
        "source_id",
        "label",
        "description",
        "manifest_version",
        "data_interval",
        "refresh_cadence",
        "supported_event_types",
        "supported_topics",
        "supported_outcomes",
        "dimensions",
        "measures",
        "generic_capabilities",
        "identity_quality",
    }
    assert [field.name for field in public.dimensions] == ["channel"]
    assert "email" not in public.model_dump_json()
    assert "masking_policy" not in public.model_dump()
    assert "identity_namespace" not in public.model_dump()


def test_time_range_requires_aware_half_open_bounds() -> None:
    with pytest.raises(ValidationError):
        TimeRange(start_at=datetime(2026, 1, 1), end_at=NOW)
    with pytest.raises(ValidationError):
        TimeRange(start_at=NOW, end_at=NOW)


def test_primitive_input_uses_discriminator_and_rejects_extra_fields() -> None:
    catalog = CatalogSourcesInput(primitive="catalog_sources")
    assert catalog.primitive == "catalog_sources"
    aggregate = AggregateEventsInput(
        primitive="aggregate_events",
        aggregation="count",
        group_by=["channel"],
        time_grain="day",
    )
    parsed = PRIMITIVE_INPUT_ADAPTER.validate_python(aggregate.model_dump())
    assert isinstance(parsed, AggregateEventsInput)
    with pytest.raises(ValidationError):
        CatalogSourcesInput(primitive="catalog_sources", unexpected="field")
