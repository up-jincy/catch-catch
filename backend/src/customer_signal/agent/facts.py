"""Shared construction of private run facts from typed analytics results."""

from __future__ import annotations

from collections.abc import Sequence

from customer_signal.agent.contracts import (
    MetricFact,
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
MATCHED_CUSTOMER_METRIC_LABEL = "완전한 Journey 패턴 고객 수"


def _distinct_metric_facts(values: Sequence[MetricFact]) -> tuple[MetricFact, ...]:
    distinct: list[MetricFact] = []
    for value in values:
        if value not in distinct:
            distinct.append(value)
    return tuple(distinct)


def _metric_facts(
    catalog: CatalogSourcesResult,
    aggregate: AggregateResult,
    matched: PatternMatchResult,
    ranked: RankCustomersResult,
    journey: CustomerJourneyResult | None,
    evidence: EvidenceResult | None,
) -> dict[str, tuple[MetricFact, ...]]:
    facts: dict[str, tuple[MetricFact, ...]] = {}

    def add(result_id: str, values: Sequence[MetricFact]) -> None:
        if result_id not in facts:
            facts[result_id] = _distinct_metric_facts(values)

    add(
        catalog.result_id,
        [
            MetricFact(label="조회 Source 수", value=len(catalog.sources), unit="개"),
            MetricFact(label="Catalog 검색 행 수", value=catalog.stats.scanned_rows, unit="행"),
            MetricFact(label="Catalog 반환 행 수", value=catalog.stats.returned_rows, unit="행"),
            *(
                MetricFact(
                    label=f"{source.source_id} 이벤트 수",
                    value=source.row_count,
                    unit="건",
                )
                for source in catalog.sources
            ),
        ],
    )
    add(
        aggregate.result_id,
        [
            MetricFact(label="집계 검색 행 수", value=aggregate.stats.scanned_rows, unit="행"),
            MetricFact(label="집계 Bucket 수", value=aggregate.stats.returned_rows, unit="개"),
            *(
                MetricFact(
                    label=f"{aggregate.group_by}:{bucket.value} 이벤트 수",
                    value=bucket.event_count,
                    unit="건",
                )
                for bucket in aggregate.buckets
            ),
            *(
                MetricFact(
                    label=f"{aggregate.group_by}:{bucket.value} 고객 수",
                    value=bucket.customer_count,
                    unit="명",
                )
                for bucket in aggregate.buckets
            ),
        ],
    )
    add(
        matched.result_id,
        [
            MetricFact(label="Journey 후보 고객 수", value=matched.candidate_count, unit="명"),
            MetricFact(
                label=MATCHED_CUSTOMER_METRIC_LABEL,
                value=matched.customer_count,
                unit="명",
            ),
            MetricFact(label="Journey 검색 행 수", value=matched.stats.scanned_rows, unit="행"),
            MetricFact(label="Journey 반환 고객 수", value=matched.stats.returned_rows, unit="명"),
        ],
    )
    add(
        ranked.result_id,
        [
            MetricFact(label="Ranking 후보 고객 수", value=ranked.candidate_count, unit="명"),
            MetricFact(label="Ranking 고객 수", value=ranked.customer_count, unit="명"),
            MetricFact(label="Ranking 검색 행 수", value=ranked.stats.scanned_rows, unit="행"),
            MetricFact(label="Ranking 반환 고객 수", value=ranked.stats.returned_rows, unit="명"),
        ],
    )
    if journey is not None:
        add(
            journey.result_id,
            [
                MetricFact(label="Journey 검색 행 수", value=journey.stats.scanned_rows, unit="행"),
                MetricFact(label="Journey Event 수", value=journey.stats.returned_rows, unit="건"),
            ],
        )
    if evidence is not None:
        add(
            evidence.result_id,
            [
                MetricFact(
                    label="Evidence 검색 행 수", value=evidence.stats.scanned_rows, unit="행"
                ),
                MetricFact(
                    label="Evidence 반환 건수", value=evidence.stats.returned_rows, unit="건"
                ),
            ],
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
        allowed_metrics_by_result=_metric_facts(
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


__all__ = ["MATCHED_CUSTOMER_METRIC_LABEL", "SIGNAL_SOURCES", "build_run_facts"]
