"""Registry-shape RED tests for the ten generic primitive handlers."""

from __future__ import annotations

from typing import get_args

from customer_signal.analytics.primitives import HANDLERS, HandlerSpec
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


EXPECTED_HANDLERS = {
    "catalog_sources": (CatalogSourcesInput, CatalogSourcesPayload),
    "profile_events": (ProfileEventsInput, ProfileEventsPayload),
    "aggregate_events": (AggregateEventsInput, AggregateEventsPayload),
    "segment_customers": (SegmentCustomersInput, SegmentCustomersPayload),
    "detect_repetition": (DetectRepetitionInput, RepetitionPayload),
    "match_sequence": (MatchSequenceInput, SequenceMatchPayload),
    "compare_segments": (CompareSegmentsInput, SegmentComparisonPayload),
    "rank_customers": (RankCustomersInput, CustomerRankingPayload),
    "get_customer_journey": (GetCustomerJourneyInput, CustomerJourneyPayload),
    "get_evidence": (GetEvidenceInput, EvidencePayload),
}


def test_handler_registry_has_exact_ten_discriminator_bound_specs() -> None:
    assert len(HANDLERS) == 10
    assert set(HANDLERS) == set(EXPECTED_HANDLERS)
    for primitive, (input_type, output_type) in EXPECTED_HANDLERS.items():
        spec = HANDLERS[primitive]
        assert isinstance(spec, HandlerSpec)
        assert spec.input_type is input_type
        assert spec.output_type is output_type
        assert get_args(input_type.model_fields["primitive"].annotation) == (primitive,)
        assert get_args(output_type.model_fields["kind"].annotation) == (primitive,)
        assert callable(spec.handler)
