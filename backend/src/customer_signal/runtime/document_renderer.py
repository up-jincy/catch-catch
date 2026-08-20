"""Pure document and Markdown projections derived from Run Artifact JSON."""

from __future__ import annotations

import html
from collections.abc import Iterable

from customer_signal.domain.reports import CustomerSignalReport, InsightReport
from customer_signal.runtime.artifacts import (
    ArtifactDocument,
    ArtifactDocumentProvenance,
    ArtifactDocumentScope,
    RunArtifact,
    artifact_json_bytes,
)


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
    lines = [
        f"# {_inline(document.headline)}",
        "",
        f"- Run ID: {_inline(document.run_id)}",
        f"- Status: {_inline(document.status)}",
        f"- Created: {_inline(document.created_at.isoformat())}",
        f"- Updated: {_inline(document.updated_at.isoformat())}",
    ]
    if document.completed_at is not None:
        lines.append(f"- Completed: {_inline(document.completed_at.isoformat())}")

    lines.extend(["", "## 질문", "", _inline(document.question)])
    lines.extend(
        [
            "",
            "## 범위",
            "",
            f"- Start: {_inline(document.scope.start_at.isoformat())}",
            f"- End \\(exclusive\\): {_inline(document.scope.end_at.isoformat())}",
            f"- Sources: {_joined(document.scope.source_ids)}",
        ]
    )
    _render_goal(lines, document)
    _render_plan(lines, document)
    _render_notes(lines, document)
    _render_report(lines, document)
    _render_provenance(lines, document)
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
    return "\n".join(lines).rstrip()


def render_markdown_bytes(artifact: RunArtifact) -> bytes:
    return f"{render_markdown(artifact)}\n".encode()


def _render_goal(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 분석 목표", ""])
    if document.goal is None:
        if document.clarification is not None:
            lines.append(f"- Clarification: {_inline(document.clarification.question)}")
            if document.clarification.answer is not None:
                lines.append(f"- Answer: {_inline(document.clarification.answer)}")
        else:
            lines.append("기록 없음")
        return
    goal = document.goal
    lines.extend(
        [
            f"- Goal ID: {_inline(goal.goal_id)}",
            f"- Objective: {_inline(goal.objective)}",
            f"- Population: {_inline(goal.population.description)}",
            f"- Output: {_inline(goal.output)}",
        ]
    )
    for measure in goal.measures:
        lines.append(
            f"- Measure: {_inline(measure.label)} \\({_inline(measure.metric_key)}, "
            f"{_inline(measure.aggregation)}, {_inline(measure.unit)}\\)"
        )


def _render_plan(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 분석 계획", ""])
    if document.plan is None:
        lines.append("기록 없음")
        return
    lines.append(
        f"Plan {_inline(document.plan.plan_id)}, revision {_inline(document.plan.revision)}"
    )
    for index, step in enumerate(document.plan.steps, start=1):
        lines.extend(
            [
                "",
                f"### {index}\\. {_inline(step.primitive)}",
                "",
                f"- Step ID: {_inline(step.step_id)}",
                f"- Sources: {_joined(step.source_ids)}",
                f"- Inputs: {_joined(step.input_step_ids) if step.input_step_ids else '없음'}",
                f"- Required metrics: {_joined(step.expected_output.required_metric_keys)}",
            ]
        )


def _render_notes(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 단계별 분석 기록", ""])
    if not document.notes:
        lines.append("기록 없음")
        return
    for index, note in enumerate(document.notes, start=1):
        lines.extend(
            [
                f"### {index}\\. {_inline(note.objective)}",
                "",
                f"- Step: {_inline(note.step_id)}",
                f"- Sources: {_joined(note.source_ids)}",
                f"- Result IDs: {_joined(note.result_ids)}",
                f"- Duration: {_inline(note.duration_ms)} ms",
            ]
        )
        for claim in note.claims:
            lines.append(f"- Claim: {_inline(claim.rendered_text)}")
        if note.evidence_ids:
            lines.append(f"- Evidence: {_joined(note.evidence_ids)}")
        lines.append("")
    if lines[-1] == "":
        lines.pop()


def _render_report(lines: list[str], document: ArtifactDocument) -> None:
    lines.extend(["", "## 보고서", ""])
    report = document.report
    if report is None:
        lines.append("완료된 보고서 없음")
        return
    lines.extend([f"### {_inline(report.headline)}", "", _inline(report.executive_summary)])
    if report.metrics:
        lines.extend(["", "### 지표", ""])
        for metric in report.metrics:
            unit = f" {_inline(metric.unit)}" if metric.unit else ""
            lines.append(f"- {_inline(metric.label)}: {_inline(metric.value)}{unit}")
    if isinstance(report, CustomerSignalReport):
        if report.findings:
            lines.extend(["", "### 검증된 발견", ""])
            for finding in report.findings:
                lines.append(f"- {_inline(finding.statement)}")
        if report.recommendations:
            lines.extend(["", "### 권장 조치", ""])
            for recommendation in report.recommendations:
                lines.append(f"- {_inline(recommendation.title)}: {_inline(recommendation.reason)}")
    elif isinstance(report, InsightReport):
        if report.findings:
            lines.extend(["", "### 발견", ""])
            for finding in report.findings:
                lines.append(f"- {_inline(finding.title)}: {_inline(finding.description)}")
        if report.recommendations:
            lines.extend(["", "### 권장 조치", ""])
            for recommendation in report.recommendations:
                lines.append(f"- {_inline(recommendation.title)}: {_inline(recommendation.reason)}")


def _render_provenance(lines: list[str], document: ArtifactDocument) -> None:
    provenance = document.provenance
    lines.extend(
        [
            "",
            "## 출처와 재현 정보",
            "",
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
