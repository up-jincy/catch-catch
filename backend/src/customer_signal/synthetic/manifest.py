"""Source manifests for the deterministic built-in synthetic dataset."""

from __future__ import annotations

from datetime import timedelta

from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.sources import (
    IdentityQuality,
    MaskingPolicy,
    RefreshDescriptor,
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
_ALL_CAPABILITIES: list[GenericPrimitiveName] = [
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
]


def synthetic_source_manifest(
    source_id: SourceId, events: list[CustomerEvent]
) -> SourceManifest:
    """Build one declared manifest from legacy synthetic events without new columns."""

    source_events = [event for event in events if event.source_id == source_id]
    if not source_events:
        raise ValueError(f"synthetic source {source_id} has no events")
    if source_id not in _LABELS:
        raise ValueError(f"unknown synthetic source {source_id}")
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
        refresh_cadence=RefreshDescriptor(cadence="static", max_lag_minutes=0),
        supported_event_types=sorted({event.event_type for event in source_events}),
        supported_topics=sorted({event.topic for event in source_events}),
        supported_outcomes=sorted({event.outcome for event in source_events}),
        dimensions=[],
        measures=[],
        generic_capabilities=_ALL_CAPABILITIES,
        masking_policy=MaskingPolicy(field_masks={"customer_ref": "masked"}),
        identity_quality=IdentityQuality(
            level="synthetic",
            description="Deterministic identity graph for demonstration data.",
            namespace="synthetic_customer",
        ),
    )


__all__ = ["synthetic_source_manifest"]
