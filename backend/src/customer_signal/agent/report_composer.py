"""Canonical public report composition from verified MCP analytics results."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from customer_signal.agent.contracts import RunFacts, RunRequest, UnsupportedClaimError
from customer_signal.agent.facts import MATCHED_CUSTOMER_METRIC_LABEL, SIGNAL_SOURCES
from customer_signal.analytics.models import (
    AggregateResult,
    CatalogSourcesResult,
    CustomerJourneyResult,
    EvidenceResult,
    PatternMatchResult,
    RankCustomersResult,
)
from customer_signal.domain.models import SourceId
from customer_signal.domain.reports import (
    AnalysisScope,
    Finding,
    InsightReport,
    Metric,
    Recommendation,
    Signal,
    SignalContribution,
)


def _source_contributions(
    matched: PatternMatchResult,
    enabled_sources: Sequence[SourceId],
) -> list[SignalContribution]:
    if not matched.customers:
        return []

    grouped: dict[SourceId, list[Signal]] = {}
    for signal in matched.customers[0].signals:
        source_id = SIGNAL_SOURCES.get(signal.code)
        if source_id is None or source_id not in enabled_sources:
            continue
        grouped.setdefault(source_id, []).append(signal.model_copy(deep=True))

    return [
        SignalContribution(
            source_id=source_id,
            score=sum(signal.score for signal in grouped[source_id]),
            signals=grouped[source_id],
        )
        for source_id in enabled_sources
        if source_id in grouped
    ]


def _selected_evidence_id(
    matched: PatternMatchResult,
    journey: CustomerJourneyResult,
    evidence: EvidenceResult,
) -> str:
    representative = matched.customers[0]
    if journey.customer_id != representative.customer_id:
        raise UnsupportedClaimError("대표 고객과 Journey 고객 ID가 일치하지 않습니다.")

    fetched_ids = set(evidence.evidence_ids)
    representative_ids = set(representative.evidence_ids)
    selected = next(
        (
            evidence_id
            for evidence_id in reversed(journey.evidence_ids)
            if evidence_id in representative_ids and evidence_id in fetched_ids
        ),
        None,
    )
    if selected is None:
        raise UnsupportedClaimError("대표 Journey를 뒷받침하는 Evidence가 없습니다.")
    selected_events = [event for event in journey.events if event.evidence_id == selected]
    if journey.evidence_ids.count(selected) != 1 or len(selected_events) != 1:
        raise UnsupportedClaimError("대표 Evidence에 대응하는 Journey Event가 유일하지 않습니다.")
    return selected


def _validate_match_and_rank(
    matched: PatternMatchResult,
    ranked: RankCustomersResult,
) -> None:
    ranked_customer_ids = [customer.customer_id for customer in ranked.customers]
    ranked_evidence_ids = [
        evidence_id for customer in ranked.customers for evidence_id in customer.evidence_ids
    ]
    if (
        ranked.customer_count != min(ranked.candidate_count, 100)
        or ranked.customer_count != len(ranked.customers)
        or ranked.stats.returned_rows != len(ranked.customers)
        or len(ranked_customer_ids) != len(set(ranked_customer_ids))
        or ranked.evidence_ids != ranked_evidence_ids
        or len(ranked.evidence_ids) != len(set(ranked.evidence_ids))
    ):
        raise UnsupportedClaimError("Ranking 결과 내부 집계가 반환 고객과 일치하지 않습니다.")
    if matched.candidate_count != ranked.candidate_count:
        raise UnsupportedClaimError("Journey Pattern과 Ranking 후보 수가 일치하지 않습니다.")

    ranked_by_customer_id = {customer.customer_id: customer for customer in ranked.customers}
    if any(
        ranked_by_customer_id.get(customer.customer_id) != customer
        for customer in matched.customers
    ):
        raise UnsupportedClaimError("Journey Pattern 고객이 Ranking 결과와 일치하지 않습니다.")


def compose_verified_report(
    request: RunRequest,
    *,
    catalog: CatalogSourcesResult,
    aggregate: AggregateResult,
    matched: PatternMatchResult,
    ranked: RankCustomersResult,
    journey: CustomerJourneyResult | None,
    evidence: EvidenceResult | None,
) -> InsightReport:
    """Build every public core claim from typed MCP results, never model prose."""

    returned_customer_ids = [customer.customer_id for customer in matched.customers]
    if (
        matched.customer_count != len(matched.customers)
        or matched.customer_ids != returned_customer_ids
        or len(returned_customer_ids) != len(set(returned_customer_ids))
    ):
        raise UnsupportedClaimError("Journey Pattern 고객 집계가 반환 고객과 일치하지 않습니다.")
    _validate_match_and_rank(matched, ranked)

    customer_count = matched.customer_count
    selected_evidence_id: str | None = None
    if customer_count > 0:
        if journey is None or evidence is None or not matched.customers:
            raise UnsupportedClaimError(
                "양수 Journey 결과에는 대표 Journey와 Evidence가 필요합니다."
            )
        selected_evidence_id = _selected_evidence_id(matched, journey, evidence)
    elif journey is not None or evidence is not None:
        raise UnsupportedClaimError("0명 Journey 결과에는 상세 Tool 결과를 포함할 수 없습니다.")

    present_sources = {source.source_id for source in catalog.sources}
    sources_used = [
        source_id for source_id in request.enabled_sources if source_id in present_sources
    ]
    top_topic: str | None = None
    if aggregate.buckets:
        top_topic = min(
            aggregate.buckets,
            key=lambda bucket: (-bucket.event_count, bucket.value),
        ).value

    if customer_count > 0:
        headline = f"검색 실패 후 문의로 이어진 고객 {customer_count}명"
        topic_summary = f" 주요 집계 Topic은 '{top_topic}'입니다." if top_topic else ""
        executive_summary = f"요청 기간에 완전한 Journey 패턴이 확인됐습니다.{topic_summary}"
        findings = [
            Finding(
                title="완전한 Journey 패턴 확인",
                description=(
                    f"검색 실패와 후속 문의 조건을 모두 충족한 고객은 {customer_count}명입니다."
                ),
                confidence="high",
                evidence_ids=[cast(str, selected_evidence_id)],
            )
        ]
        recommendations = [
            Recommendation(
                action_id="care_call",
                title="대표 고위험 고객 후속 확인",
                reason="반복 검색과 미해결 문의가 연결된 대표 Journey가 확인됐습니다.",
                evidence_ids=[cast(str, selected_evidence_id)],
            )
        ]
    else:
        headline = "검색 실패 후 문의로 이어진 고객 0명"
        executive_summary = "활성 Source 범위에서는 완전한 Journey 패턴이 확인되지 않았습니다."
        findings = []
        recommendations = []

    missing_sources = list(dict.fromkeys(matched.missing_sources))
    limitations: list[str] = []
    if aggregate.stats.scanned_rows == 0:
        limitations.append("요청 기간에 분석 가능한 데이터가 없습니다.")
    elif journey is None:
        if ranked.candidate_count:
            limitations.append(
                f"완전한 패턴은 없지만 부분 Journey 후보 {ranked.candidate_count}명이 확인됐습니다."
            )
        else:
            limitations.append("대표 Journey로 확인할 고객 후보가 없습니다.")
    limitations.extend(
        f"{source_id} Source가 없어 완전한 패턴 판단이 제한됩니다." for source_id in missing_sources
    )
    for source_id in request.enabled_sources:
        if source_id not in present_sources and source_id not in missing_sources:
            limitations.append(f"{source_id} Source에 요청 기간 데이터가 없습니다.")

    return InsightReport(
        analysis_type="journey",
        scope=AnalysisScope(
            start_at=request.start_at,
            end_at=request.end_at,
            enabled_sources=list(request.enabled_sources),
            population_description="검색 실패 후 같은 Topic의 후속 문의 Journey",
        ),
        headline=headline,
        executive_summary=executive_summary,
        metrics=[
            Metric(
                label=MATCHED_CUSTOMER_METRIC_LABEL,
                value=customer_count,
                unit="명",
                result_id=matched.result_id,
            )
        ],
        findings=findings,
        signal_contributions=_source_contributions(matched, request.enabled_sources),
        ranked_customers=[customer.model_copy(deep=True) for customer in matched.customers],
        representative_journeys=(
            [event.model_copy(deep=True) for event in journey.events] if journey is not None else []
        ),
        representative_journey_ids=[journey.result_id] if journey is not None else [],
        recommendations=recommendations,
        sources_used=sources_used,
        limitations=limitations,
    )


def apply_verified_model_narrative(
    canonical: InsightReport,
    draft: InsightReport,
    facts: RunFacts,
) -> InsightReport:
    """Publish model prose only when it exactly matches the verified server narrative."""

    if not canonical.findings:
        return canonical

    canonical_evidence_ids = {
        evidence_id for finding in canonical.findings for evidence_id in finding.evidence_ids
    }
    if not canonical_evidence_ids or not canonical_evidence_ids.issubset(
        facts.fetched_evidence_ids
    ):
        raise UnsupportedClaimError("검증된 보고서의 Evidence 출처가 완전하지 않습니다.")

    if (
        draft.headline != canonical.headline
        or draft.executive_summary != canonical.executive_summary
        or draft.findings != canonical.findings
        or draft.recommendations != canonical.recommendations
    ):
        raise UnsupportedClaimError("Gemini 설명이 검증된 보고서와 일치하지 않습니다.")

    published = canonical.model_copy(deep=True)
    published.headline = draft.headline
    published.executive_summary = draft.executive_summary
    published.findings = [finding.model_copy(deep=True) for finding in draft.findings]
    published.recommendations = [
        recommendation.model_copy(deep=True) for recommendation in draft.recommendations
    ]
    return published


__all__ = ["apply_verified_model_narrative", "compose_verified_report"]
