"""Locked manifests for each deterministic built-in synthetic source."""

from __future__ import annotations

from datetime import timedelta

from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.sources import (
    DimensionDescriptor,
    IdentityQualityDescriptor,
    MaskingPolicy,
    SourceManifest,
    TimeRange,
)
from customer_signal.domain.types import GenericPrimitiveName, SourceId


_LABELS = {
    "search_history": "Search history",
    "search_feedback": "Search feedback",
    "digital_behavior": "Digital behavior",
    "subscription": "Subscription",
    "voc": "Voice of customer",
}
_DESCRIPTIONS = {
    "search_history": "Search activity from the customer support journey.",
    "search_feedback": "Feedback captured after support search interactions.",
    "digital_behavior": "Authenticated digital support behavior.",
    "subscription": "Subscription review and product account activity.",
    "voc": "Voice-of-customer support cases.",
}
_IDENTITY_QUALITY = {
    "search_history": ("search_run", "exact"),
    "search_feedback": ("search_run", "exact"),
    "digital_behavior": ("digital_session", "declared"),
    "subscription": ("subscription_entry", "exact"),
    "voc": ("voc_case", "declared"),
}
_ALL_CAPABILITIES: frozenset[GenericPrimitiveName] = frozenset(
    {
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
)


def synthetic_source_manifest(source_id: SourceId, events: list[CustomerEvent]) -> SourceManifest:
    """Build one manifest compatible with the legacy synthetic Event columns."""

    source_events = [event for event in events if event.source_id == source_id]
    if not source_events:
        raise ValueError(f"synthetic source {source_id} has no events")
    if source_id not in _LABELS:
        raise ValueError(f"unknown synthetic source {source_id}")
    namespace, link_method = _IDENTITY_QUALITY[source_id]
    return SourceManifest(
        source_id=source_id,
        label=_LABELS[source_id],
        description=_DESCRIPTIONS[source_id],
        adapter_version="1",
        manifest_version="1",
        data_interval=TimeRange(
            start_at=min(event.occurred_at for event in source_events),
            end_at=max(event.occurred_at for event in source_events) + timedelta(microseconds=1),
        ),
        refresh_cadence="static_demo",
        supported_event_types=frozenset(event.event_type for event in source_events),
        supported_topics=frozenset(event.topic for event in source_events),
        supported_outcomes=frozenset(event.outcome for event in source_events),
        dimensions={
            "customer_ref": DimensionDescriptor(
                semantic_type="identifier",
                description="Masked source-native customer reference.",
                pii_classification="direct_identifier",
            )
        },
        measures={},
        capabilities=_ALL_CAPABILITIES,
        masking_policy=MaskingPolicy(rules={"customer_ref": "partial"}),
        identity_quality=IdentityQualityDescriptor(
            namespace=namespace,
            link_method=link_method,
            confidence=1.0,
        ),
    )


__all__ = ["synthetic_source_manifest"]
