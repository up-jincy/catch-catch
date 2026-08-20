"""Exact server-owned registry for the ten generic analysis primitives."""

from __future__ import annotations

from types import MappingProxyType

from customer_signal.analytics.primitives.common import HandlerSpec, PrimitiveContext
from customer_signal.analytics.primitives.evidence import get_evidence
from customer_signal.analytics.primitives.profile import (
    aggregate_events,
    catalog_sources,
    profile_events,
)
from customer_signal.analytics.primitives.ranking import (
    get_customer_journey,
    rank_customers,
)
from customer_signal.analytics.primitives.segments import compare_segments, segment_customers
from customer_signal.analytics.primitives.sequences import detect_repetition, match_sequence
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    CatalogSourcesPayload,
    CustomerJourneyPayload,
    CustomerRankingPayload,
    EvidencePayload,
    ProfileEventsPayload,
    RepetitionPayload,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
    SequenceMatchPayload,
)
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    CompareSegmentsInput,
    DetectRepetitionInput,
    GetCustomerJourneyInput,
    GetEvidenceInput,
    MatchSequenceInput,
    ProfileEventsInput,
    RankCustomersInput,
    SegmentCustomersInput,
)
from customer_signal.domain.types import GenericPrimitiveName


HANDLERS: MappingProxyType[GenericPrimitiveName, HandlerSpec] = MappingProxyType(
    {
        "catalog_sources": HandlerSpec(
            CatalogSourcesInput,
            CatalogSourcesPayload,
            catalog_sources,
        ),
        "profile_events": HandlerSpec(
            ProfileEventsInput,
            ProfileEventsPayload,
            profile_events,
        ),
        "aggregate_events": HandlerSpec(
            AggregateEventsInput,
            AggregateEventsPayload,
            aggregate_events,
        ),
        "segment_customers": HandlerSpec(
            SegmentCustomersInput,
            SegmentCustomersPayload,
            segment_customers,
        ),
        "detect_repetition": HandlerSpec(
            DetectRepetitionInput,
            RepetitionPayload,
            detect_repetition,
        ),
        "match_sequence": HandlerSpec(
            MatchSequenceInput,
            SequenceMatchPayload,
            match_sequence,
        ),
        "compare_segments": HandlerSpec(
            CompareSegmentsInput,
            SegmentComparisonPayload,
            compare_segments,
        ),
        "rank_customers": HandlerSpec(
            RankCustomersInput,
            CustomerRankingPayload,
            rank_customers,
        ),
        "get_customer_journey": HandlerSpec(
            GetCustomerJourneyInput,
            CustomerJourneyPayload,
            get_customer_journey,
        ),
        "get_evidence": HandlerSpec(
            GetEvidenceInput,
            EvidencePayload,
            get_evidence,
        ),
    }
)


__all__ = ["HANDLERS", "HandlerSpec", "PrimitiveContext"]
