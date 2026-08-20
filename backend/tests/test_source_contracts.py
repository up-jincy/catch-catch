"""Contract tests for portable source and primitive domain models."""

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from customer_signal.agent.contracts import RunRequest
from customer_signal.domain.models import CustomerEvent, IdentityRef
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    PRIMITIVE_INPUT_ADAPTER,
)
from customer_signal.domain.sources import (
    DimensionDescriptor,
    IdentityQualityDescriptor,
    MaskingPolicy,
    MeasureDescriptor,
    PublicSourceList,
    PublicSourceManifest,
    SourceManifest,
    TimeRange,
)
from customer_signal.domain.types import GenericPrimitiveName, SourceId


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _manifest() -> SourceManifest:
    return SourceManifest(
        source_id="partner_crm",
        label="Partner CRM",
        description="Partner-provided CRM activity.",
        adapter_version="1.0",
        manifest_version="1.0",
        data_interval=TimeRange(start_at=NOW, end_at=NOW + timedelta(days=1)),
        refresh_cadence="daily",
        supported_event_types=frozenset({"search"}),
        supported_topics=frozenset({"pricing"}),
        supported_outcomes=frozenset({"none"}),
        dimensions={
            "channel": DimensionDescriptor(
                semantic_type="category",
                description="Interaction channel",
                pii_classification="none",
                allowed_values=frozenset({"web", "app"}),
            ),
            "email": DimensionDescriptor(
                semantic_type="identifier",
                description="Raw address",
                pii_classification="direct_identifier",
            ),
        },
        measures={
            "amount": MeasureDescriptor(
                semantic_type="number",
                description="Transaction amount",
                unit="KRW",
            )
        },
        capabilities=frozenset({"aggregate_events"}),
        masking_policy=MaskingPolicy(rules={"email": "hash"}),
        identity_quality=IdentityQualityDescriptor(
            namespace="partner_user",
            link_method="declared",
            confidence=0.8,
        ),
    )


def _event(**updates: object) -> CustomerEvent:
    values: dict[str, object] = {
        "event_id": "evt-1",
        "evidence_id": "ev-1",
        "source_id": "partner_crm",
        "occurred_at": NOW,
        "event_type": "search",
        "action": "view",
        "topic": "pricing",
        "outcome": "none",
        "text": "Viewed pricing",
        "canonical_customer_id": "cus-1",
        "identities": [IdentityRef(namespace="partner_user", value="p-1")],
        "dimensions": {"channel": "web"},
        "measures": {"amount": 12.5},
    }
    values.update(updates)
    return CustomerEvent.model_validate(values)


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


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("dimensions", {"channel": b"web"}),
        ("dimensions", {"channel": 1.5}),
        ("measures", {"amount": "12.5"}),
        ("measures", {"amount": b"12.5"}),
        ("measures", {"amount": True}),
        ("measures", {"amount": float("inf")}),
        ("measures", {"amount": float("nan")}),
    ],
)
def test_customer_event_rejects_coercible_or_nonfinite_semantic_values(
    field_name: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _event(**{field_name: value})


def test_customer_event_accepts_strict_dimensions_and_finite_measures() -> None:
    event = _event(dimensions={"channel": "web", "attempts": 2, "known": True})
    assert event.dimensions == {"channel": "web", "attempts": 2, "known": True}
    assert event.measures == {"amount": 12.5}


def test_manifest_rejects_undeclared_event_semantics_and_identity_namespace() -> None:
    manifest = _manifest()
    manifest.validate_event(_event())

    with pytest.raises(ValueError, match="undeclared dimension"):
        manifest.validate_event(_event(dimensions={"region": "KR"}))
    with pytest.raises(ValueError, match="undeclared measure"):
        manifest.validate_event(_event(measures={"count": 1}))
    with pytest.raises(ValueError, match="allowed values"):
        manifest.validate_event(_event(dimensions={"channel": "store"}))
    with pytest.raises(ValueError, match="identity namespace"):
        manifest.validate_event(
            _event(identities=[IdentityRef(namespace="another_partner", value="p-1")])
        )


def test_manifest_enforces_descriptor_refresh_masking_and_identity_contracts() -> None:
    values = _manifest().model_dump()

    with pytest.raises(ValidationError):
        SourceManifest.model_validate({**values, "refresh_cadence": "monthly"})
    with pytest.raises(ValidationError):
        DimensionDescriptor(
            semantic_type="currency",
            description="Not an approved semantic type",
            pii_classification="none",
        )
    with pytest.raises(ValidationError):
        MeasureDescriptor(
            semantic_type="number",
            description="Missing required unit",
            unit="",
        )
    with pytest.raises(ValidationError):
        SourceManifest.model_validate({**values, "masking_policy": {"rules": {"unknown": "hash"}}})
    with pytest.raises(ValidationError):
        SourceManifest.model_validate({**values, "masking_policy": {"rules": {"email": "encrypt"}}})
    with pytest.raises(ValidationError):
        IdentityQualityDescriptor(
            namespace="partner_user", link_method="declared", confidence=float("inf")
        )
    with pytest.raises(ValidationError):
        IdentityQualityDescriptor(namespace="partner_user", link_method="heuristic", confidence=0.5)


def test_public_manifest_has_locked_shape_and_excludes_private_source_details() -> None:
    public = PublicSourceManifest.from_internal(_manifest())
    assert set(public.model_dump()) == {
        "source_id",
        "label",
        "description",
        "data_interval",
        "refresh_cadence",
        "supported_event_types",
        "supported_topics",
        "supported_outcomes",
        "dimensions",
        "measures",
        "capabilities",
        "adapter_version",
        "manifest_version",
    }
    assert set(public.dimensions) == {"channel"}
    assert set(public.measures) == {"amount"}
    public_json = public.model_dump_json()
    assert "email" not in public_json
    assert "partner_user" not in public_json
    assert "masking_policy" not in public_json
    assert "rules" not in public_json

    source_list = PublicSourceList(items=[public])
    assert set(source_list.model_dump()) == {"items"}
    assert source_list.items == [public]


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
