"""Deterministic fixture analysis orchestrated through the real MCP boundary."""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from time import perf_counter
from typing import Any, Protocol, cast

from fastmcp import Client, FastMCP

from customer_signal.agent.contracts import (
    EventEmitter,
    ReportValidator,
    RunRequest,
    RunnerOutcome,
    UnsupportedClaimError,
    UnsupportedQuestionError,
)
from customer_signal.agent.facts import SIGNAL_SOURCES, build_run_facts
from customer_signal.agent.validator import validate_report
from customer_signal.analytics.models import (
    AggregateResult,
    AnalyticsResultModel,
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
    JourneyEvent,
    Metric,
    RankedCustomer,
    Recommendation,
    Signal,
    SignalContribution,
)
from customer_signal.runtime.events import RunnerEvent


_SEARCH_INTENT_TERMS = ("검색",)
_FAILED_OR_UNRESOLVED_TERMS = (
    "검색실패",
    "검색에실패",
    "검색에서실패",
    "검색으로실패",
    "검색이실패",
    "검색은실패",
    "검색도실패",
    "해결하지못",
    "해결못",
    "해결이안",
    "해결안",
    "해결되지않",
    "해결되지못",
    "미해결",
    "찾지못",
    "못찾",
    "풀리지않",
    "풀지못",
    "답이없",
    "답변이없",
    "결과가없",
    "결과없",
    "만족하지못",
    "불만족",
)
_CONTACT_TRANSITION_TERMS = (
    "고객센터에",
    "고객센터로",
    "고객센터까지",
    "고객지원센터에",
    "고객지원센터로",
    "고객지원센터까지",
    "콜센터",
    "상담",
    "문의",
    "voc",
)
_OPPOSITE_INTENT_TERMS = (
    "성공",
    "해결완료",
    "정상처리",
    "정상적으로해결",
    "문제없이",
    "만족함",
    "만족한",
    "만족했",
    "해결하고",
    "해결한",
    "해결된",
    "해결됐",
    "해결되어",
    "해결되었",
    "답을찾았",
    "답변을얻었",
    "답변을받았",
)
_OTHER_ANALYSIS_TERMS = (
    "신규가입",
    "매출",
    "예측",
    "전망",
    "로밍",
    "해지",
    "인터넷품질",
)
_PLAN_STEPS = [
    "분석 가능한 Source와 기간 확인",
    "Topic별 이벤트 집계",
    "검색 실패 후 문의 Journey 패턴 확인",
    "고객 위험 신호 순위 확인",
    "대표 고객 Journey와 Evidence 확인",
    "Run 근거와 최종 보고서 검증",
]


class ToolCaller(Protocol):
    """The small subset of an already-connected FastMCP client used by the runner."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...


async def _emit(emit: EventEmitter, event: RunnerEvent) -> None:
    pending = emit(event)
    if inspect.isawaitable(pending):
        await pending


def _supports(question: str) -> bool:
    normalized = "".join(question.casefold().split())
    if any(term in normalized for term in _OTHER_ANALYSIS_TERMS):
        return False

    resolution_scope = normalized
    for term in _FAILED_OR_UNRESOLVED_TERMS:
        resolution_scope = resolution_scope.replace(term, "")
    if any(term in resolution_scope for term in _OPPOSITE_INTENT_TERMS):
        return False

    return (
        any(term in normalized for term in _SEARCH_INTENT_TERMS)
        and any(term in normalized for term in _FAILED_OR_UNRESOLVED_TERMS)
        and any(term in normalized for term in _CONTACT_TRANSITION_TERMS)
    )


def _assert_selected_evidence(
    selected_evidence_id: str,
    selected_event: JourneyEvent,
    evidence: EvidenceResult,
) -> None:
    expected_ids = [selected_evidence_id]
    record_ids = [record.evidence_id for record in evidence.records]
    if evidence.evidence_ids != expected_ids or record_ids != expected_ids:
        raise UnsupportedClaimError(
            "Evidence 결과가 선택한 대표 Evidence ID와 정확히 일치하지 않습니다."
        )
    if evidence.records[0].source_id != selected_event.source_id:
        raise UnsupportedClaimError(
            "Evidence Source가 선택한 Journey Event Source와 일치하지 않습니다."
        )


def _bind_representative_journey(
    representative: RankedCustomer,
    journey: CustomerJourneyResult,
) -> tuple[str, JourneyEvent]:
    if journey.customer_id != representative.customer_id:
        raise UnsupportedClaimError("대표 고객과 Journey 고객 ID가 일치하지 않습니다.")

    representative_evidence = set(representative.evidence_ids)
    selected_evidence_id = next(
        (
            evidence_id
            for evidence_id in reversed(journey.evidence_ids)
            if evidence_id in representative_evidence
        ),
        None,
    )
    if selected_evidence_id is None:
        raise UnsupportedClaimError("대표 고객과 Journey가 공유하는 Evidence ID가 없습니다.")
    selected_events = [
        event for event in journey.events if event.evidence_id == selected_evidence_id
    ]
    if journey.evidence_ids.count(selected_evidence_id) != 1 or len(selected_events) != 1:
        raise UnsupportedClaimError(
            "선택한 Evidence ID에 대응하는 Journey Event가 정확히 하나여야 합니다."
        )
    return selected_evidence_id, selected_events[0]


def _source_contributions(
    matched: PatternMatchResult,
    enabled_sources: Sequence[SourceId],
) -> list[SignalContribution]:
    """Summarize signals for the top matched representative customer only."""

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


def _build_report(
    request: RunRequest,
    *,
    catalog: CatalogSourcesResult,
    aggregate: AggregateResult,
    matched: PatternMatchResult,
    journey: CustomerJourneyResult | None,
    selected_evidence_id: str | None,
) -> InsightReport:
    if (journey is None) != (selected_evidence_id is None):
        raise UnsupportedClaimError("대표 Journey와 Evidence 선택은 함께 존재해야 합니다.")
    if matched.customer_count and selected_evidence_id is None:
        raise UnsupportedClaimError("일치 고객 보고서에 대표 Evidence가 없습니다.")

    present_sources = {source.source_id for source in catalog.sources}
    sources_used = [
        source_id for source_id in request.enabled_sources if source_id in present_sources
    ]
    customer_count = matched.customer_count
    top_topic: str | None = None
    if aggregate.buckets:
        aggregate_top = min(
            aggregate.buckets,
            key=lambda bucket: (-bucket.event_count, bucket.value),
        )
        top_topic = aggregate_top.value

    if customer_count:
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
        if selected_evidence_id is None:
            recommendations = []
        else:
            recommendations = [
                Recommendation(
                    action_id="further_analysis",
                    title="부분 Journey 후보 추가 분석",
                    reason="검색 신호가 있는 대표 후보를 확인하고 누락 Source를 보완해야 합니다.",
                    evidence_ids=[selected_evidence_id],
                )
            ]

    missing_sources = list(dict.fromkeys(matched.missing_sources))
    limitations = []
    if aggregate.stats.scanned_rows == 0:
        limitations.append("요청 기간에 분석 가능한 데이터가 없습니다.")
    elif journey is None:
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
                label="완전한 Journey 패턴 고객 수",
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


class FixtureRunner:
    """Execute the bounded four-to-six-tool path through one FastMCP session."""

    def __init__(
        self,
        server: FastMCP,
        *,
        validator: ReportValidator = validate_report,
    ) -> None:
        self._server = server
        self._validator = validator

    async def _call_tool[T: AnalyticsResultModel](
        self,
        client: ToolCaller,
        *,
        name: str,
        arguments: dict[str, Any],
        source: Sequence[SourceId],
        result_type: type[T],
        emit: EventEmitter,
    ) -> T:
        await _emit(
            emit,
            RunnerEvent(
                type="tool_started",
                payload={"tool": name, "source": list(source)},
            ),
        )
        started_at = perf_counter()
        response = await client.call_tool(name, arguments)
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        if response.is_error or not isinstance(response.structured_content, dict):
            raise RuntimeError(f"MCP tool failed: {name}")
        result = result_type.model_validate_json(
            json.dumps(response.structured_content, ensure_ascii=False)
        )
        await _emit(
            emit,
            RunnerEvent(
                type="tool_completed",
                payload={
                    "tool": name,
                    "source": list(source),
                    "count": result.stats.returned_rows,
                    "duration_ms": duration_ms,
                    "result_id": result.result_id,
                },
            ),
        )
        return result

    async def run(
        self,
        request: RunRequest,
        *,
        emit: EventEmitter,
    ) -> RunnerOutcome:
        if not _supports(request.question):
            error = UnsupportedQuestionError("검색 실패와 고객 문의 Journey 질문만 지원합니다.")
            await _emit(
                emit,
                RunnerEvent(
                    type="error",
                    payload={"code": error.code, "message": str(error)},
                ),
            )
            raise error

        await _emit(emit, RunnerEvent(type="plan", payload={"steps": _PLAN_STEPS}))
        scope = {
            "start_at": request.start_at.isoformat(),
            "end_at": request.end_at.isoformat(),
            "enabled_sources": list(request.enabled_sources),
        }

        try:
            async with Client(self._server) as client:
                caller = cast(ToolCaller, client)
                catalog = await self._call_tool(
                    caller,
                    name="catalog_sources",
                    arguments={
                        "start_at": scope["start_at"],
                        "end_at": scope["end_at"],
                    },
                    source=[],
                    result_type=CatalogSourcesResult,
                    emit=emit,
                )
                aggregate = await self._call_tool(
                    caller,
                    name="aggregate_events",
                    arguments={**scope, "group_by": "topic"},
                    source=request.enabled_sources,
                    result_type=AggregateResult,
                    emit=emit,
                )
                matched = await self._call_tool(
                    caller,
                    name="match_journey_pattern",
                    arguments=scope,
                    source=request.enabled_sources,
                    result_type=PatternMatchResult,
                    emit=emit,
                )
                ranked = await self._call_tool(
                    caller,
                    name="rank_customers",
                    arguments=scope,
                    source=request.enabled_sources,
                    result_type=RankCustomersResult,
                    emit=emit,
                )

                journey: CustomerJourneyResult | None = None
                evidence: EvidenceResult | None = None
                selected_evidence_id: str | None = None
                representative_customer_id: str | None = None
                representatives = matched.customers or ranked.customers
                if representatives:
                    representative = representatives[0]
                    representative_customer_id = representative.customer_id
                    journey = await self._call_tool(
                        caller,
                        name="get_customer_journey",
                        arguments={**scope, "customer_id": representative.customer_id},
                        source=request.enabled_sources,
                        result_type=CustomerJourneyResult,
                        emit=emit,
                    )
                    selected_evidence_id, selected_event = _bind_representative_journey(
                        representative,
                        journey,
                    )
                    evidence = await self._call_tool(
                        caller,
                        name="get_evidence",
                        arguments={"evidence_ids": [selected_evidence_id]},
                        source=[selected_event.source_id],
                        result_type=EvidenceResult,
                        emit=emit,
                    )
                    _assert_selected_evidence(
                        selected_evidence_id,
                        selected_event,
                        evidence,
                    )

            report = _build_report(
                request,
                catalog=catalog,
                aggregate=aggregate,
                matched=matched,
                journey=journey,
                selected_evidence_id=selected_evidence_id,
            )
            facts = build_run_facts(
                request,
                catalog=catalog,
                aggregate=aggregate,
                matched=matched,
                ranked=ranked,
                journey=journey,
                evidence=evidence,
                representative_customer_id=representative_customer_id,
            )
            await _emit(
                emit,
                RunnerEvent(
                    type="validating",
                    payload={"result_ids": list(facts.tool_result_ids.values())},
                ),
            )
            self._validator(report, facts)
            outcome = RunnerOutcome(report=report, facts=facts)
            await _emit(
                emit,
                RunnerEvent(
                    type="result",
                    payload={
                        "agent_mode": outcome.agent_mode,
                        "report": report.model_dump(mode="json"),
                    },
                ),
            )
            return outcome
        except Exception as error:
            if isinstance(error, (UnsupportedClaimError, UnsupportedQuestionError)):
                code = error.code
                message = str(error)
            else:
                code = "tool_execution_failed"
                message = "분석 Tool 실행에 실패했습니다."
            await _emit(
                emit,
                RunnerEvent(
                    type="error",
                    payload={"code": code, "message": message},
                ),
            )
            raise


__all__ = ["FixtureRunner", "ToolCaller"]
