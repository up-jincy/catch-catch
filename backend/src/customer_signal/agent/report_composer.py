"""Canonical public report composition from verified MCP analytics results."""

from __future__ import annotations

import json
from collections.abc import Sequence

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
from customer_signal.domain.analysis import AnalysisNote, CustomerSignalReportDraft
from customer_signal.domain.facts import (
    AnalysisFact,
    AnalysisMetricFact,
    CustomerJourneyPayload,
    CustomerRankingPayload,
)
from customer_signal.domain.reports import (
    AnalysisFinding,
    AnalysisRecommendation,
    AnalysisReportProvenance,
    AnalysisScope,
    CustomerSignalReport,
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


def _validated_evidence_ids(
    matched: PatternMatchResult,
    journey: CustomerJourneyResult,
    evidence: EvidenceResult,
) -> list[str]:
    representative = matched.customers[0]
    if journey.customer_id != representative.customer_id:
        raise UnsupportedClaimError("대표 고객과 Journey 고객 ID가 일치하지 않습니다.")

    fetched_ids = list(evidence.evidence_ids)
    representative_ids = set(representative.evidence_ids)
    journey_ids = set(journey.evidence_ids)
    selected_ids = [
        evidence_id
        for evidence_id in fetched_ids
        if evidence_id in representative_ids and evidence_id in journey_ids
    ]
    if not fetched_ids or len(fetched_ids) != len(set(fetched_ids)) or not selected_ids:
        raise UnsupportedClaimError("대표 Journey를 뒷받침하는 Evidence가 없습니다.")
    for evidence_id in selected_ids:
        selected_events = [event for event in journey.events if event.evidence_id == evidence_id]
        if journey.evidence_ids.count(evidence_id) != 1 or len(selected_events) != 1:
            raise UnsupportedClaimError(
                "대표 Evidence에 대응하는 Journey Event가 유일하지 않습니다."
            )
    return selected_ids


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
    selected_evidence_ids: list[str] = []
    if customer_count > 0:
        if journey is None or evidence is None or not matched.customers:
            raise UnsupportedClaimError(
                "양수 Journey 결과에는 대표 Journey와 Evidence가 필요합니다."
            )
        selected_evidence_ids = _validated_evidence_ids(matched, journey, evidence)
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
                evidence_ids=list(selected_evidence_ids),
            )
        ]
        recommendations = [
            Recommendation(
                action_id="care_call",
                title="대표 고위험 고객 후속 확인",
                reason="반복 검색과 미해결 문의가 연결된 대표 Journey가 확인됐습니다.",
                evidence_ids=list(selected_evidence_ids),
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

    def evidence_is_verified_subset(evidence_ids: list[str]) -> bool:
        return (
            bool(evidence_ids)
            and len(evidence_ids) == len(set(evidence_ids))
            and set(evidence_ids) <= facts.fetched_evidence_ids
        )

    findings_match = len(draft.findings) == len(canonical.findings) and all(
        draft_finding.title == canonical_finding.title
        and draft_finding.description == canonical_finding.description
        and draft_finding.confidence == canonical_finding.confidence
        and evidence_is_verified_subset(draft_finding.evidence_ids)
        for draft_finding, canonical_finding in zip(
            draft.findings,
            canonical.findings,
            strict=True,
        )
    )
    recommendations_match = len(draft.recommendations) == len(canonical.recommendations) and all(
        draft_recommendation.action_id == canonical_recommendation.action_id
        and draft_recommendation.title == canonical_recommendation.title
        and draft_recommendation.reason == canonical_recommendation.reason
        and evidence_is_verified_subset(draft_recommendation.evidence_ids)
        for draft_recommendation, canonical_recommendation in zip(
            draft.recommendations,
            canonical.recommendations,
            strict=True,
        )
    )
    if (
        draft.headline != canonical.headline
        or draft.executive_summary != canonical.executive_summary
        or not findings_match
        or not recommendations_match
    ):
        raise UnsupportedClaimError("Gemini 설명이 검증된 보고서와 일치하지 않습니다.")

    published = canonical.model_copy(deep=True)
    published.headline = draft.headline
    published.executive_summary = draft.executive_summary
    published.findings = [
        draft_finding.model_copy(
            update={"evidence_ids": list(canonical_finding.evidence_ids)},
            deep=True,
        )
        for draft_finding, canonical_finding in zip(
            draft.findings,
            canonical.findings,
            strict=True,
        )
    ]
    published.recommendations = [
        draft_recommendation.model_copy(
            update={"evidence_ids": list(canonical_recommendation.evidence_ids)},
            deep=True,
        )
        for draft_recommendation, canonical_recommendation in zip(
            draft.recommendations,
            canonical.recommendations,
            strict=True,
        )
    ]
    return published


def compose_customer_signal_report(
    *,
    goal,
    facts: Sequence[AnalysisFact],
    notes: Sequence[AnalysisNote],
    draft: CustomerSignalReportDraft,
) -> CustomerSignalReport:
    """Compose generic publication only from verified Claims and server-owned Facts."""

    if draft.goal_id != goal.goal_id:
        raise UnsupportedClaimError("보고서 Goal이 현재 분석 Goal과 일치하지 않습니다.")
    fact_by_id = {fact.fact_id: fact for fact in facts}
    if len(fact_by_id) != len(facts) or not facts:
        raise UnsupportedClaimError("보고서에는 고유한 검증 Fact가 필요합니다.")
    claim_by_id = {claim.claim_id: claim for note in notes for claim in note.claims}
    if len(claim_by_id) != sum(len(note.claims) for note in notes):
        raise UnsupportedClaimError("보고서 Claim ID는 Run 안에서 고유해야 합니다.")
    unknown_claims = set(draft.claim_refs) - set(claim_by_id)
    if unknown_claims:
        raise UnsupportedClaimError("보고서가 현재 Run에 없는 Claim을 참조합니다.")

    selected_claims = [claim_by_id[claim_id] for claim_id in draft.claim_refs]
    findings: list[AnalysisFinding] = []
    for claim in selected_claims:
        claim_fact_ids = _stable_unique(reference.fact_id for reference in claim.fact_refs)
        if not set(claim_fact_ids) <= set(fact_by_id):
            raise UnsupportedClaimError("보고서 Claim이 현재 Run 밖의 Fact를 참조합니다.")
        evidence_ids = _stable_unique(
            [
                *(
                    reference.evidence_id
                    for reference in claim.fact_refs
                    if reference.evidence_id is not None
                ),
                *(
                    evidence_id
                    for fact_id in claim_fact_ids
                    for evidence_id in fact_by_id[fact_id].evidence_ids
                ),
            ]
        )
        findings.append(
            AnalysisFinding(
                claim=claim,
                statement=claim.rendered_text,
                fact_ids=claim_fact_ids,
                evidence_ids=evidence_ids,
            )
        )

    recommendations: list[AnalysisRecommendation] = []
    for action in draft.recommended_actions:
        if not set(action.fact_refs) <= set(fact_by_id):
            raise UnsupportedClaimError("권장 조치가 현재 Run 밖의 Fact를 참조합니다.")
        if not set(action.claim_refs) <= set(draft.claim_refs):
            raise UnsupportedClaimError("권장 조치가 선택되지 않은 Claim을 참조합니다.")
        evidence_ids = _stable_unique(
            evidence_id
            for fact_id in action.fact_refs
            for evidence_id in fact_by_id[fact_id].evidence_ids
        )
        recommendations.append(
            AnalysisRecommendation(
                action_id=action.action_id,
                title=_action_title(action.action_id),
                reason=(
                    "검증된 분석 Claim을 근거로 후속 조치를 검토합니다: "
                    + "; ".join(
                        claim_by_id[claim_id].rendered_text for claim_id in action.claim_refs
                    )
                ),
                claim_ids=list(action.claim_refs),
                fact_ids=list(action.fact_refs),
                evidence_ids=evidence_ids,
            )
        )

    metrics = _collect_metrics(facts)
    limitations = _stable_unique(limitation for note in notes for limitation in note.limitations)
    provenance = _build_generic_provenance(facts)
    executive_summary = (
        f"목표 '{goal.objective}'에 대해 {len(facts)}개의 검증 Fact를 수집하고 "
        f"{len(selected_claims)}개의 Claim을 검증했습니다."
    )
    if selected_claims:
        executive_summary += f" 핵심 확인 사실: {selected_claims[-1].rendered_text}."
    return CustomerSignalReport(
        goal=goal,
        headline=_generic_headline(goal.objective, selected_claims, facts),
        executive_summary=executive_summary,
        metrics=metrics,
        signals=[
            signal
            for fact in facts
            if isinstance(fact.payload, CustomerRankingPayload)
            for customer in fact.payload.customers
            for signal in customer.signals
        ],
        ranked_customers=[
            customer
            for fact in facts
            if isinstance(fact.payload, CustomerRankingPayload)
            for customer in fact.payload.customers
        ],
        representative_journeys=[
            event
            for fact in facts
            if isinstance(fact.payload, CustomerJourneyPayload)
            for event in fact.payload.events
        ],
        findings=findings,
        recommendations=recommendations,
        limitations=limitations,
        provenance=provenance,
    )


def _collect_metrics(facts: Sequence[AnalysisFact]) -> list[AnalysisMetricFact]:
    metrics: list[AnalysisMetricFact] = []
    seen: set[str] = set()
    for fact in facts:
        for metric in fact.metrics:
            key = json.dumps(
                metric.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if key not in seen:
                metrics.append(metric.model_copy(deep=True))
                seen.add(key)
    return metrics


def _build_generic_provenance(facts: Sequence[AnalysisFact]) -> AnalysisReportProvenance:
    adapter_versions: dict[str, str] = {}
    manifest_versions: dict[str, str] = {}
    for fact in facts:
        for source_id, version in fact.payload.provenance.adapter_versions.items():
            if source_id in adapter_versions and adapter_versions[source_id] != version:
                raise UnsupportedClaimError("Run 안의 Adapter version이 일치하지 않습니다.")
            adapter_versions[source_id] = version
        for source_id, version in fact.payload.provenance.manifest_versions.items():
            if source_id in manifest_versions and manifest_versions[source_id] != version:
                raise UnsupportedClaimError("Run 안의 Manifest version이 일치하지 않습니다.")
            manifest_versions[source_id] = version
    source_ids = _stable_unique(source for fact in facts for source in fact.source_ids)
    return AnalysisReportProvenance(
        fact_ids=_stable_unique(fact.fact_id for fact in facts),
        result_ids=_stable_unique(fact.result_id for fact in facts),
        source_ids=source_ids,
        dataset_versions=_stable_unique(fact.payload.provenance.dataset_version for fact in facts),
        adapter_versions=adapter_versions,
        manifest_versions=manifest_versions,
    )


def _generic_headline(objective: str, claims, facts: Sequence[AnalysisFact]) -> str:
    if claims:
        claim = claims[-1]
        if claim.claim_type == "metric" and claim.fact_refs:
            reference = claim.fact_refs[0]
            fact = next((fact for fact in facts if fact.fact_id == reference.fact_id), None)
            if fact is not None and reference.metric_key is not None:
                metric = fact.metric(reference.metric_key)
                return f"{metric.label}: {metric.value} {metric.unit}"
    return objective


def _action_title(action_id: str) -> str:
    labels = {
        "further_analysis": "후속 분석 범위 검토",
        "customer_followup": "대상 고객 후속 확인",
        "journey_improvement": "고객 Journey 개선 검토",
    }
    return labels.get(action_id, f"{action_id.replace('_', ' ')} 검토")


def _stable_unique(values) -> list:
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


__all__ = [
    "apply_verified_model_narrative",
    "compose_customer_signal_report",
    "compose_verified_report",
]
