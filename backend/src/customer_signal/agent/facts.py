"""Shared construction of private run facts from typed analytics results."""

from __future__ import annotations

from collections.abc import Sequence

from customer_signal.agent.contracts import (
    MetricFactValue,
    RunFacts,
    RunRequest,
    ToolName,
    UnsupportedClaimError,
)
from customer_signal.analytics.models import (
    AggregateResult,
    CatalogSourcesResult,
    CustomerJourneyResult,
    EvidenceResult,
    PatternMatchResult,
    RankCustomersResult,
)
from customer_signal.domain.models import SourceId


SIGNAL_SOURCES: dict[str, SourceId] = {
    "failed_search": "search_history",
    "repeated_failed_search": "search_history",
    "negative_feedback": "search_feedback",
    "unresolved_voc": "voc",
}


def _distinct_metric_values(
    values: Sequence[MetricFactValue],
) -> tuple[MetricFactValue, ...]:
    distinct: list[MetricFactValue] = []
    for value in values:
        if not any(type(value) is type(existing) and value == existing for existing in distinct):
            distinct.append(value)
    return tuple(distinct)


def _metric_facts(
    catalog: CatalogSourcesResult,
    aggregate: AggregateResult,
    matched: PatternMatchResult,
    ranked: RankCustomersResult,
    journey: CustomerJourneyResult | None,
    evidence: EvidenceResult | None,
) -> dict[str, tuple[MetricFactValue, ...]]:
    facts: dict[str, tuple[MetricFactValue, ...]] = {}

    def add(result_id: str, values: Sequence[MetricFactValue]) -> None:
        if result_id not in facts:
            facts[result_id] = _distinct_metric_values(values)

    add(
        catalog.result_id,
        [
            catalog.stats.scanned_rows,
            catalog.stats.returned_rows,
            *(source.row_count for source in catalog.sources),
        ],
    )
    add(
        aggregate.result_id,
        [
            aggregate.stats.scanned_rows,
            aggregate.stats.returned_rows,
            *(bucket.event_count for bucket in aggregate.buckets),
            *(bucket.customer_count for bucket in aggregate.buckets),
        ],
    )
    add(
        matched.result_id,
        [
            matched.candidate_count,
            matched.customer_count,
            matched.stats.scanned_rows,
            matched.stats.returned_rows,
        ],
    )
    add(
        ranked.result_id,
        [
            ranked.candidate_count,
            ranked.customer_count,
            ranked.stats.scanned_rows,
            ranked.stats.returned_rows,
        ],
    )
    if journey is not None:
        add(
            journey.result_id,
            [journey.stats.scanned_rows, journey.stats.returned_rows],
        )
    if evidence is not None:
        add(
            evidence.result_id,
            [evidence.stats.scanned_rows, evidence.stats.returned_rows],
        )
    return facts


def _validate_evidence_provenance(
    journey: CustomerJourneyResult,
    evidence: EvidenceResult,
) -> None:
    record_ids = [record.evidence_id for record in evidence.records]
    if (
        evidence.evidence_ids != record_ids
        or len(record_ids) != len(set(record_ids))
        or not set(record_ids) <= set(journey.evidence_ids)
    ):
        raise UnsupportedClaimError("Evidence 결과가 Journey Evidence와 일치하지 않습니다.")
    journey_events = {event.evidence_id: event for event in journey.events}
    if any(
        (event := journey_events.get(record.evidence_id)) is None
        or event.source_id != record.source_id
        or event.occurred_at != record.occurred_at
        for record in evidence.records
    ):
        raise UnsupportedClaimError("Evidence 출처가 Journey Event와 일치하지 않습니다.")


def build_run_facts(
    request: RunRequest,
    *,
    catalog: CatalogSourcesResult,
    aggregate: AggregateResult,
    matched: PatternMatchResult,
    ranked: RankCustomersResult,
    journey: CustomerJourneyResult | None,
    evidence: EvidenceResult | None,
    representative_customer_id: str | None,
) -> RunFacts:
    """Bind private authorization facts to exact typed MCP results."""

    if (journey is None) != (evidence is None):
        raise UnsupportedClaimError("Journey와 Evidence Tool 결과는 함께 존재해야 합니다.")
    if journey is not None:
        if journey.customer_id != representative_customer_id:
            raise UnsupportedClaimError("대표 고객과 Journey 고객 ID가 일치하지 않습니다.")
        assert evidence is not None
        _validate_evidence_provenance(journey, evidence)

    allowed_evidence_ids = {
        evidence_id for customer in matched.customers for evidence_id in customer.evidence_ids
    }
    if journey is not None:
        allowed_evidence_ids.update(journey.evidence_ids)
    allowed_customer_ids = {customer.customer_id for customer in ranked.customers}
    allowed_customer_ids.update(matched.customer_ids)
    if representative_customer_id is not None:
        allowed_customer_ids.add(representative_customer_id)

    tool_result_ids: dict[ToolName, str] = {
        "catalog_sources": catalog.result_id,
        "aggregate_events": aggregate.result_id,
        "match_journey_pattern": matched.result_id,
        "rank_customers": ranked.result_id,
    }
    if journey is not None and evidence is not None:
        tool_result_ids["get_customer_journey"] = journey.result_id
        tool_result_ids["get_evidence"] = evidence.result_id

    fetched_evidence_ids = frozenset(evidence.evidence_ids if evidence is not None else [])
    evidence_source_facts = (
        {record.evidence_id: record.source_id for record in evidence.records}
        if evidence is not None
        else {}
    )
    evidence_customer_facts = (
        {record.evidence_id: representative_customer_id for record in evidence.records}
        if evidence is not None and representative_customer_id is not None
        else {}
    )

    return RunFacts(
        tool_result_ids=tool_result_ids,
        allowed_customer_ids=frozenset(allowed_customer_ids),
        allowed_evidence_ids=frozenset(allowed_evidence_ids),
        fetched_evidence_ids=fetched_evidence_ids,
        allowed_sources=frozenset(request.enabled_sources),
        allowed_metric_values_by_result=_metric_facts(
            catalog,
            aggregate,
            matched,
            ranked,
            journey,
            evidence,
        ),
        ranked_customer_facts={
            customer.customer_id: customer.model_copy(deep=True) for customer in matched.customers
        },
        representative_journey_result_ids=(
            frozenset([journey.result_id]) if journey is not None else frozenset()
        ),
        journey_event_facts=(
            {event.event_id: event.model_copy(deep=True) for event in journey.events}
            if journey is not None
            else {}
        ),
        signal_source_facts={
            signal.code: source_id
            for customer in matched.customers
            for signal in customer.signals
            if (source_id := SIGNAL_SOURCES.get(signal.code)) is not None
        },
        evidence_source_facts=evidence_source_facts,
        evidence_customer_facts=evidence_customer_facts,
    )


__all__ = ["SIGNAL_SOURCES", "build_run_facts"]
