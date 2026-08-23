"""Pure document and Markdown projections derived from Run Artifact JSON."""

from __future__ import annotations

import html
import json
from collections.abc import Iterable

from customer_signal.domain.analysis import AnalysisStep
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisFact,
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
from customer_signal.domain.reports import CustomerSignalReport, InsightReport
from customer_signal.runtime.artifacts import (
    ArtifactDocument,
    ArtifactDocumentProvenance,
    ArtifactDocumentScope,
    RunArtifact,
    artifact_json_bytes,
)

_PRIMITIVE_LABELS: dict[str, str] = {
    "catalog_sources": "Source 카탈로그 확인",
    "profile_events": "이벤트 분포 프로파일링",
    "aggregate_events": "이벤트 집계",
    "segment_customers": "고객 Segment 분할",
    "detect_repetition": "반복 행동 탐지",
    "match_sequence": "행동 순서 매칭",
    "compare_segments": "Segment 비교",
    "rank_customers": "고객 우선순위 산정",
    "get_customer_journey": "대표 Journey 조회",
    "get_evidence": "Evidence 조회",
}

_STATUS_LABELS: dict[str, str] = {
    "queued": "대기 중",
    "running": "분석 진행 중",
    "awaiting_clarification": "확인 답변 대기",
    "completed": "분석 완료",
    "degraded": "제한 조건으로 완료",
    "failed": "분석 실패",
}


def _primitive_label(primitive: str) -> str:
    return _PRIMITIVE_LABELS.get(primitive, primitive)


def artifact_result_ids(artifact: RunArtifact) -> list[str]:
    return _stable_unique(fact.result_id for fact in artifact.facts)


def artifact_fact_ids(artifact: RunArtifact) -> list[str]:
    return _stable_unique(fact.fact_id for fact in artifact.facts)


def render_document(artifact: RunArtifact) -> ArtifactDocument:
    """Create a deterministic document projection without external reads or model calls."""

    # Rehydrate the JSON contract so the persisted representation remains the only source of truth.
    artifact = RunArtifact.model_validate_json(artifact_json_bytes(artifact))
    source_ids = _stable_unique(
        [
            *artifact.request.enabled_sources,
            *(source for fact in artifact.facts for source in fact.source_ids),
        ]
    )
    evidence_ids = _stable_unique(
        evidence_id for fact in artifact.facts for evidence_id in fact.evidence_ids
    )
    limitations = list(artifact.limitations)
    for note in artifact.notes:
        _extend_unique(limitations, note.limitations)
    if artifact.report is not None:
        _extend_unique(limitations, artifact.report.limitations)

    headline = artifact.request.question
    if artifact.clarification is not None:
        headline = artifact.clarification.question
    if artifact.goal is not None:
        headline = artifact.goal.objective
    if artifact.report is not None:
        headline = artifact.report.headline

    return ArtifactDocument(
        run_id=artifact.run_id,
        status=artifact.status,
        headline=headline,
        question=artifact.request.question,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
        completed_at=artifact.completed_at,
        scope=ArtifactDocumentScope(
            start_at=artifact.request.start_at,
            end_at=artifact.request.end_at,
            source_ids=list(artifact.request.enabled_sources),
        ),
        goal=artifact.goal,
        clarification=artifact.clarification,
        plan=artifact.plan,
        plan_history=(
            list(artifact.plan_history)
            if artifact.plan_history
            else [artifact.plan]
            if artifact.plan is not None
            else []
        ),
        facts=list(artifact.facts),
        notes=list(artifact.notes),
        report=artifact.report,
        provenance=ArtifactDocumentProvenance(
            fact_ids=artifact_fact_ids(artifact),
            result_ids=artifact_result_ids(artifact),
            source_ids=source_ids,
            evidence_ids=evidence_ids,
            dataset_versions=list(artifact.versions.dataset_versions),
            adapter_versions=dict(artifact.versions.adapter_versions),
            manifest_versions=dict(artifact.versions.manifest_versions),
            prompt_version=artifact.versions.prompt_version,
            model_version=artifact.versions.model_version,
            last_event_id=artifact.last_event_id,
        ),
        limitations=limitations,
        error=artifact.error,
    )


def render_markdown(artifact: RunArtifact) -> str:
    """Render escaped Markdown from the pure document projection."""

    document = render_document(artifact)
    lines = [f"# {_inline(document.headline)}"]
    _render_overview(lines, document)
    _render_goal(lines, document)
    _render_process(lines, document)
    _render_report(lines, document)
    _render_recommendations(lines, document)
    _render_limitations(lines, document)
    if document.error is not None:
        lines.extend(
            [
                "",
                "## 오류",
                "",
                f"- Code: {_inline(document.error.code)}",
                f"- Message: {_inline(document.error.message)}",
            ]
        )
        if document.error.step_id is not None:
            lines.append(f"- Step: {_inline(document.error.step_id)}")
    _render_plan(lines, document)
    _render_facts(lines, document)
    _render_provenance(lines, document)
    return "\n".join(lines).rstrip()


def _render_overview(lines: list[str], document: ArtifactDocument) -> None:
    """Answer up front: what was explored, what was concluded, what to do next."""

    lines.extend(["", "## 한눈에 보기", ""])
    lines.append(f"- 질문: {_inline(document.question)}")
    if document.goal is not None:
        lines.append(f"- 분석 목표: {_inline(document.goal.objective)}")
    elif document.clarification is not None:
        lines.append(
            f"- 분석 목표: 아직 확정되지 않았습니다. 확인 질문 — "
            f"{_inline(document.clarification.question)}"
        )
    else:
        lines.append("- 분석 목표: 아직 수립되지 않았습니다.")

    report = document.report
    if report is not None:
        lines.append(f"- 결론: {_inline(report.headline)}")
    elif document.status == "running":
        lines.append("- 결론: 분석이 진행 중이라 아직 결론이 없습니다.")
    elif document.status == "awaiting_clarification":
        lines.append("- 결론: 확인 질문에 대한 답변을 기다리고 있어 아직 결론이 없습니다.")
    elif document.status == "failed":
        lines.append("- 결론: 분석이 실패해 결론을 도출하지 못했습니다.")
    else:
        lines.append("- 결론: 검증 가능한 결론 없이 기록이 종료됐습니다.")

    if report is not None and report.recommendations:
        actions = _joined(
            recommendation.title for recommendation in report.recommendations
        )
        lines.append(f"- 권장 액션: {actions}")
    else:
        lines.append("- 권장 액션: 제안된 후속 조치가 없습니다.")

    status_label = _STATUS_LABELS.get(document.status, document.status)
    lines.append(f"- 진행 상태: {_inline(status_label)}")


def _render_process(lines: list[str], document: ArtifactDocument) -> None:
    """Narrate each executed step: why it ran, what went in, what came out, what came next."""

    lines.extend(["", "## 탐색 과정 — 단계별 진행 기록", ""])
    if document.plan_history:
        first = document.plan_history[0]
        lines.append(f"최초 계획 수립 근거: {_inline(first.rationale)}")
        for plan in document.plan_history[1:]:
            lines.append("")
            lines.append(
                f"계획 수정 \\(revision {_inline(plan.revision)}\\): {_inline(plan.rationale)}"
            )
    if not document.notes:
        lines.append("" if document.plan_history else "기록 없음")
        if document.plan_history:
            lines.append("아직 완료된 단계 기록이 없습니다.")
        return

    steps = _plan_steps_by_revision(document)
    facts_by_id = {fact.fact_id: fact for fact in document.facts}
    _render_trajectory_table(lines, document, steps, facts_by_id)
    for index, note in enumerate(document.notes, start=1):
        step = steps.get((note.plan_revision, note.step_id))
        note_facts = [
            facts_by_id[fact_id] for fact_id in note.fact_ids if fact_id in facts_by_id
        ]
        primitive = _primitive_label(step.primitive) if step is not None else note.step_id
        lines.extend(["", f"### {index}단계\\. {_inline(primitive)}", ""])
        lines.append(f"- 단계 목표: {_inline(note.objective)}")
        if step is not None:
            lines.append(f"- 선택 이유: {_inline(step.selection_reason)}")
            lines.append(f"- 입력: {_inline(_describe_parameters(step))}")
        for fact in note_facts:
            processing = fact.payload.processing
            lines.append(
                f"- 출력: 이벤트 {_inline(processing.scanned_events)}건을 스캔해 "
                f"{_inline(processing.matched_events)}건이 조건과 일치, "
                f"{_inline(processing.returned_rows)}행을 반환했습니다."
            )
            for metric in fact.metrics:
                lines.append(
                    f"  - {_inline(metric.label)} = {_inline(metric.value)} {_inline(metric.unit)}"
                )
            lines.extend(
                f"  - {_inline(highlight)}" for highlight in _payload_highlights(fact)
            )
        if note.claims:
            lines.append("- 확인한 사실:")
            lines.extend(f"  - {_inline(claim.rendered_text)}" for claim in note.claims)
        else:
            lines.append("- 확인한 사실: 이 단계에서 검증된 Claim은 없습니다.")
        for limitation in note.limitations:
            lines.append(f"- 단계 한계: {_inline(limitation)}")
        lines.append(f"- 다음 행동: {_inline(note.next_action)}")
        lines.append(
            f"- 실행 정보: Source {_joined(note.source_ids)}, {_inline(note.duration_ms)} ms"
        )


def _render_trajectory_table(
    lines: list[str],
    document: ArtifactDocument,
    steps: dict[tuple[int, str], AnalysisStep],
    facts_by_id: dict[str, AnalysisFact],
) -> None:
    lines.extend(
        [
            "",
            "실행 궤적 요약:",
            "",
            "| 단계 | 실행 내용 | 입력 | 핵심 출력 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for index, note in enumerate(document.notes, start=1):
        step = steps.get((note.plan_revision, note.step_id))
        label = _primitive_label(step.primitive) if step is not None else note.step_id
        parameters = _describe_parameters(step) if step is not None else "기록 없음"
        first_fact = next(
            (facts_by_id[fact_id] for fact_id in note.fact_ids if fact_id in facts_by_id),
            None,
        )
        if first_fact is not None and first_fact.metrics:
            metric = first_fact.metrics[0]
            output = f"{metric.label} = {metric.value} {metric.unit}"
        else:
            output = "검증 Metric 없음"
        lines.append(
            f"| {_inline(index)} | {_inline(label)} | {_inline(parameters)} "
            f"| {_inline(output)} |"
        )


_PARAMETER_LABELS: dict[str, str] = {
    "aggregation": "집계 방식",
    "group_by": "그룹 기준",
    "predicates": "필터 조건",
    "sequence": "탐지할 행동 순서",
    "time_grain": "시간 단위",
    "measure": "측정 대상",
    "metric_key": "지표 키",
    "minimum_matching_events": "최소 매칭 이벤트",
    "within_hours": "시간 창(시간)",
    "top_n": "상위 N",
    "customer_id": "대상 고객",
    "segment_id": "대상 Segment",
    "evidence_ids": "대상 Evidence",
    "baseline_step_id": "기준 단계",
    "comparison_step_id": "비교 단계",
}


def _describe_parameters(step: AnalysisStep) -> str:
    """Summarize tool input as short Korean phrases instead of raw JSON."""

    dumped = step.parameters.model_dump(mode="json", exclude_none=True)
    parts = [f"대상 Source {', '.join(str(source) for source in step.source_ids)}"]
    for key, value in dumped.items():
        if key == "primitive" or value in (None, [], {}):
            continue
        label = _PARAMETER_LABELS.get(key, key)
        if key == "sequence" and isinstance(value, list):
            rendered = " → ".join(str(item) for item in value)
        elif isinstance(value, list):
            rendered = "; ".join(
                item if isinstance(item, str) else _structured_json(item) for item in value
            )
        elif isinstance(value, dict):
            rendered = _structured_json(value)
        else:
            rendered = str(value)
        parts.append(f"{label} {rendered}")
    if len(parts) == 1:
        parts.append("추가 조건 없음")
    return " · ".join(parts)


def _dimension_text(dimensions: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in dimensions.items())


def _payload_highlights(fact: AnalysisFact) -> list[str]:
    """Surface the actual result rows a step returned, not only its counters."""

    payload = fact.payload
    highlights: list[str] = []
    if isinstance(payload, CatalogSourcesPayload):
        rendered = ", ".join(
            f"{source.source_id}({source.row_count}행)" for source in payload.sources
        )
        if rendered:
            highlights.append(f"사용 가능 Source: {rendered}")
    elif isinstance(payload, ProfileEventsPayload):
        for bucket in payload.distributions[:3]:
            highlights.append(
                f"분포 {_dimension_text(bucket.dimensions)}: 이벤트 {bucket.event_count}건, "
                f"고객 {bucket.customer_count}명"
            )
    elif isinstance(payload, AggregateEventsPayload):
        for bucket in payload.buckets[:3]:
            highlights.append(
                f"집계 {_dimension_text(bucket.dimensions)}: 이벤트 {bucket.event_count}건, "
                f"고객 {bucket.customer_count}명"
            )
    elif isinstance(payload, SegmentCustomersPayload):
        highlights.append(f"Segment 고객 {len(payload.customer_ids)}명")
        for predicate, count in payload.predicate_counts.items():
            highlights.append(f"조건 '{predicate}' 충족: {count}건")
    elif isinstance(payload, RepetitionPayload):
        highlights.append(f"반복 행동이 확인된 고객 {len(payload.matches)}명")
    elif isinstance(payload, SequenceMatchPayload):
        highlights.append(f"요청한 행동 순서와 일치한 고객 {len(payload.matched_customer_ids)}명")
    elif isinstance(payload, SegmentComparisonPayload):
        for delta in payload.deltas:
            highlights.append(
                f"비교 {delta.metric_key}: 기준 {delta.baseline} → 비교 {delta.comparison} "
                f"(차이 {delta.delta} {delta.unit})"
            )
    elif isinstance(payload, CustomerRankingPayload):
        highlights.append(f"우선순위가 산정된 고객 {len(payload.customers)}명")
        for customer in payload.customers[:3]:
            highlights.append(f"상위 고객 {customer.customer_id}: score {customer.score}")
    elif isinstance(payload, CustomerJourneyPayload):
        highlights.append(
            f"고객 {payload.customer_id}의 Journey 이벤트 {len(payload.events)}건 조회"
        )
    elif isinstance(payload, EvidencePayload):
        highlights.append(f"원본 Evidence {len(payload.records)}건 확인")
    return highlights


def _render_recommendations(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 권장 액션 — 무엇을 해야 하나", ""])
    report = document.report
    if report is None or not report.recommendations:
        lines.append("제안된 후속 조치가 없습니다.")
        return
    for index, recommendation in enumerate(report.recommendations, start=1):
        lines.append(f"{index}\\. **{_inline(recommendation.title)}**")
        lines.append(f"   - 근거: {_inline(recommendation.reason)}")
        if isinstance(report, CustomerSignalReport):
            lines.append(
                f"   - 참조: Claim {_inline(len(recommendation.claim_ids))}개, "
                f"Fact {_inline(len(recommendation.fact_ids))}개, "
                f"Evidence {_inline(len(recommendation.evidence_ids))}개"
            )


def _plan_steps_by_revision(
    document: ArtifactDocument,
) -> dict[tuple[int, str], AnalysisStep]:
    steps: dict[tuple[int, str], AnalysisStep] = {}
    for plan in document.plan_history:
        for step in plan.steps:
            steps[(plan.revision, step.step_id)] = step
    return steps


def render_markdown_bytes(artifact: RunArtifact) -> bytes:
    return f"{render_markdown(artifact)}\n".encode()


def _render_goal(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(
        [
            "",
            "## 분석 목표 — 무엇을 확인하려 했나",
            "",
            f"- 질문: {_inline(document.question)}",
            f"- 분석 기간: {_inline(document.scope.start_at.isoformat())} 부터 "
            f"{_inline(document.scope.end_at.isoformat())} 전까지",
            f"- 사용 Source: {_joined(document.scope.source_ids)}",
        ]
    )
    if document.goal is None:
        if document.clarification is not None:
            lines.append(
                f"- 확인 질문: 분석 목표를 확정하기 전에 다음 답변이 필요했습니다 — "
                f"{_inline(document.clarification.question)}"
            )
            if document.clarification.answer is not None:
                lines.append(f"- 받은 답변: {_inline(document.clarification.answer)}")
        else:
            lines.append("- 목표: 기록 없음")
        return
    goal = document.goal
    lines.extend(
        [
            f"- 목표: {_inline(goal.objective)}",
            f"- 분석 대상: {_inline(goal.population.description)}",
            f"- 산출물 형태: {_inline(goal.output)}",
        ]
    )
    for measure in goal.measures:
        lines.append(
            f"- 측정 지표: {_inline(measure.label)} \\({_inline(measure.metric_key)}, "
            f"{_inline(measure.aggregation)}, {_inline(measure.unit)}\\)"
        )


def _render_plan(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 부록 A\\. 실행 계획 상세", ""])
    if not document.plan_history:
        lines.append("기록 없음")
        return
    for plan in document.plan_history:
        lines.extend(
            [
                f"### Plan {_inline(plan.plan_id)}, revision {_inline(plan.revision)}",
                "",
                f"- Rationale: {_inline(plan.rationale)}",
            ]
        )
        for index, step in enumerate(plan.steps, start=1):
            lines.extend(
                [
                    "",
                    f"#### {index}\\. {_inline(step.primitive)}",
                    "",
                    f"- Step ID: {_inline(step.step_id)}",
                    f"- Selection reason: {_inline(step.selection_reason)}",
                    f"- Parameters: {_inline(_structured_json(step.parameters))}",
                    f"- Sources: {_joined(step.source_ids)}",
                    f"- Inputs: {_joined(step.input_step_ids) if step.input_step_ids else '없음'}",
                    f"- Required metrics: {_joined(step.expected_output.required_metric_keys)}",
                ]
            )
        lines.append("")
    if lines[-1] == "":
        lines.pop()


def _render_facts(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 부록 B\\. 공개 Fact 원장", ""])
    if not document.facts:
        lines.append("기록 없음")
        return
    for index, fact in enumerate(document.facts, start=1):
        processing = fact.payload.processing
        lines.extend(
            [
                f"### {index}\\. {_inline(fact.primitive)}",
                "",
                f"- Fact ID: {_inline(fact.fact_id)}",
                f"- Step: {_inline(fact.step_id)}",
                f"- Result ID: {_inline(fact.result_id)}",
                f"- Sources: {_joined(fact.source_ids)}",
                f"- Processing: scanned={_inline(processing.scanned_events)}, "
                f"matched={_inline(processing.matched_events)}, "
                f"returned={_inline(processing.returned_rows)}",
            ]
        )
        for metric in fact.metrics:
            lines.append(
                f"- Metric: {_inline(metric.label)} ({_inline(metric.metric_key)}) = "
                f"{_inline(metric.value)} {_inline(metric.unit)}"
            )
        lines.append("")
    if lines[-1] == "":
        lines.pop()


def _render_report(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 결론 — 무엇을 알게 됐나", ""])
    report = document.report
    if report is None:
        status_label = _STATUS_LABELS.get(document.status, document.status)
        lines.append(
            f"완료된 보고서가 아직 없습니다 \\(현재 상태: {_inline(status_label)}\\)."
        )
        return
    lines.extend([f"### {_inline(report.headline)}", "", _inline(report.executive_summary)])
    if isinstance(report, CustomerSignalReport):
        if report.findings:
            lines.extend(["", "### 검증된 발견", ""])
            for finding in report.findings:
                lines.append(
                    f"- {_inline(finding.statement)} "
                    f"\\(근거 Fact {_inline(len(finding.fact_ids))}개, "
                    f"Evidence {_inline(len(finding.evidence_ids))}개\\)"
                )
    elif isinstance(report, InsightReport) and report.findings:
        lines.extend(["", "### 발견", ""])
        for finding in report.findings:
            lines.append(f"- {_inline(finding.title)}: {_inline(finding.description)}")
    if report.metrics:
        lines.extend(["", "### 핵심 지표", ""])
        for metric in report.metrics:
            unit = f" {_inline(metric.unit)}" if metric.unit else ""
            lines.append(f"- {_inline(metric.label)}: {_inline(metric.value)}{unit}")


def _render_provenance(lines: list[str], document: ArtifactDocument) -> None:
    provenance = document.provenance
    lines.extend(
        [
            "",
            "## 부록 C\\. 실행 정보와 출처",
            "",
            f"- Run ID: {_inline(document.run_id)}",
            f"- Status: {_inline(document.status)}",
            f"- Created: {_inline(document.created_at.isoformat())}",
            f"- Updated: {_inline(document.updated_at.isoformat())}",
        ]
    )
    if document.completed_at is not None:
        lines.append(f"- Completed: {_inline(document.completed_at.isoformat())}")
    lines.extend(
        [
            f"- Fact IDs: {_joined(provenance.fact_ids) if provenance.fact_ids else '없음'}",
            f"- Result IDs: {_joined(provenance.result_ids) if provenance.result_ids else '없음'}",
            f"- Sources: {_joined(provenance.source_ids) if provenance.source_ids else '없음'}",
            f"- Evidence IDs: {_joined(provenance.evidence_ids) if provenance.evidence_ids else '없음'}",
            f"- Dataset versions: "
            f"{_joined(provenance.dataset_versions) if provenance.dataset_versions else '없음'}",
            f"- Last event ID: {_inline(provenance.last_event_id)}",
        ]
    )
    if provenance.prompt_version is not None:
        lines.append(f"- Prompt version: {_inline(provenance.prompt_version)}")
    if provenance.model_version is not None:
        lines.append(f"- Model version: {_inline(provenance.model_version)}")
    for source_id, version in provenance.adapter_versions.items():
        lines.append(f"- Adapter {_inline(source_id)}: {_inline(version)}")
    for source_id, version in provenance.manifest_versions.items():
        lines.append(f"- Manifest {_inline(source_id)}: {_inline(version)}")


def _render_limitations(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 제한 사항", ""])
    if not document.limitations:
        lines.append("기록된 제한 사항 없음")
        return
    lines.extend(f"- {_inline(limitation)}" for limitation in document.limitations)


def _inline(value: object) -> str:
    normalized = " ".join(str(value).split())
    escaped = html.escape(normalized, quote=False).replace("\\", "\\\\")
    for character in "`*{}[]()#+-!|>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _joined(values: Iterable[object]) -> str:
    return ", ".join(_inline(value) for value in values)


def _structured_json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    _extend_unique(result, values)
    return result


def _extend_unique(target: list[str], values: Iterable[str]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)


__all__ = [
    "ArtifactDocument",
    "ArtifactDocumentProvenance",
    "ArtifactDocumentScope",
    "artifact_fact_ids",
    "artifact_result_ids",
    "render_document",
    "render_markdown",
    "render_markdown_bytes",
]
