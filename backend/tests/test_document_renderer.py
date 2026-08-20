"""Pure Artifact-to-document and Markdown rendering tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from customer_signal.agent.contracts import RunRequest
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNote,
    AnalysisPlan,
    AnalysisStep,
    ContinueAfterStep,
    ExpectedOutputSpec,
    FactRef,
    MeasureSpec,
    PopulationSpec,
    PublicRunError,
    StepLimits,
    VerifiedClaim,
)
from customer_signal.domain.facts import (
    AnalysisMetricFact,
    FactProvenance,
    ProcessingStats,
    SegmentCustomersPayload,
    build_fact,
)
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    ProfileEventsInput,
)
from customer_signal.domain.reports import (
    AnalysisFinding,
    AnalysisReportProvenance,
    AnalysisScope,
    CustomerSignalReport,
    InsightReport,
    Metric,
)
from customer_signal.domain.sources import EventScope, TimeRange
from customer_signal.runtime.artifacts import (
    ClarificationRecord,
    RunArtifact,
    RunVersions,
    artifact_json_bytes,
)
from customer_signal.runtime.document_renderer import (
    artifact_result_ids,
    render_document,
    render_markdown,
    render_markdown_bytes,
)


NOW = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
SCOPE = EventScope(
    start_at=NOW - timedelta(days=30),
    end_at=NOW,
    source_ids=["voc"],
    max_events=100,
)
LIMITS = StepLimits(
    max_input_events=100,
    max_output_rows=20,
    max_evidence=5,
    timeout_seconds=10.0,
)


def _request(question: str = "불만이 반복된 고객 수를 알려줘") -> RunRequest:
    return RunRequest(
        question=question,
        start_at=SCOPE.start_at,
        end_at=SCOPE.end_at,
        enabled_sources=["voc"],
    )


def _goal() -> AnalysisGoal:
    return AnalysisGoal(
        goal_id="goal-document",
        objective="반복 불만 고객 규모를 확인한다",
        population=PopulationSpec(description="선택 기간 고객"),
        time_range=TimeRange(start_at=SCOPE.start_at, end_at=SCOPE.end_at),
        source_ids=["voc"],
        measures=[
            MeasureSpec(
                metric_key="segment_customer_count",
                label="Segment customers",
                aggregation="count",
                unit="customers",
            )
        ],
        output="aggregate",
    )


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="plan-document",
        revision=0,
        goal_id="goal-document",
        steps=[
            AnalysisStep(
                step_id="step-catalog",
                primitive="catalog_sources",
                parameters=CatalogSourcesInput(primitive="catalog_sources"),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="catalog_sources", required_metric_keys=["source_count"]
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
            AnalysisStep(
                step_id="step-profile",
                primitive="profile_events",
                parameters=ProfileEventsInput(primitive="profile_events"),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="profile_events",
                    required_metric_keys=["customer_count", "event_count"],
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
            AnalysisStep(
                step_id="step-aggregate",
                primitive="aggregate_events",
                parameters=AggregateEventsInput(
                    primitive="aggregate_events", aggregation="count", time_grain="day"
                ),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="aggregate_events", required_metric_keys=["event_count"]
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
        ],
    )


def _fact():
    metric = AnalysisMetricFact(
        metric_key="segment_customer_count",
        label="Segment customers",
        value=2,
        unit="customers",
    )
    payload = SegmentCustomersPayload(
        kind="segment_customers",
        processing=ProcessingStats(scanned_events=10, matched_events=2, returned_rows=2),
        provenance=FactProvenance(
            scope=SCOPE,
            source_ids=["voc"],
            adapter_versions={"voc": "adapter-1"},
            manifest_versions={"voc": "manifest-1"},
            dataset_version="dataset-1",
        ),
        metrics=[metric],
        segment_id="segment-repeat",
        customer_ids=["customer-1", "customer-2"],
        predicate_counts={"repeated_complaint": 2},
    )
    return build_fact(
        fact_id="fact-segment",
        step_id="step-aggregate",
        primitive="segment_customers",
        result_id="segment_customers:document",
        payload=payload,
        scope=SCOPE,
        created_at=NOW - timedelta(seconds=1),
    )


def _claim(fact) -> VerifiedClaim:
    return VerifiedClaim(
        claim_type="metric",
        subject="segment_customer_count",
        operator="eq",
        target=2,
        fact_refs=[
            FactRef(
                fact_id=fact.fact_id,
                result_id=fact.result_id,
                metric_key="segment_customer_count",
                label="Segment customers",
                unit="customers",
                plan_revision=0,
            )
        ],
        claim_id="claim-aaaaaaaaaaaaaaaaaaaaaaaa",
        rendered_text="Segment customers: 2 customers",
    )


def _note(fact, claim: VerifiedClaim) -> AnalysisNote:
    return AnalysisNote(
        note_id="note-bbbbbbbbbbbbbbbbbbbbbbbb",
        step_id="step-aggregate",
        objective="반복 불만 Segment를 집계했습니다.",
        fact_ids=[fact.fact_id],
        claims=[claim],
        next_step_id=None,
        limitations=[],
        source_ids=["voc"],
        result_ids=[fact.result_id],
        evidence_ids=[],
        started_at=NOW - timedelta(seconds=2),
        completed_at=NOW - timedelta(seconds=1),
        duration_ms=1_000,
        plan_revision=0,
    )


def _artifact() -> RunArtifact:
    goal = _goal()
    fact = _fact()
    claim = _claim(fact)
    report = CustomerSignalReport(
        goal=goal,
        headline="반복 불만 고객 2명",
        executive_summary="검증된 공개 Fact에서 두 고객을 확인했습니다.",
        metrics=fact.metrics,
        findings=[
            AnalysisFinding(
                claim=claim,
                statement="반복 불만 Segment에는 두 고객이 있습니다.",
                fact_ids=[fact.fact_id],
            )
        ],
        limitations=[],
        provenance=AnalysisReportProvenance(
            fact_ids=[fact.fact_id],
            result_ids=[fact.result_id],
            source_ids=["voc"],
            dataset_versions=["dataset-1"],
            adapter_versions={"voc": "adapter-1"},
            manifest_versions={"voc": "manifest-1"},
        ),
    )
    return RunArtifact(
        run_id=RUN_ID,
        status="completed",
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW,
        completed_at=NOW,
        request=_request(),
        goal=goal,
        plan=_plan(),
        facts=[fact],
        notes=[_note(fact, claim)],
        report=report,
        last_event_id=9,
        versions=RunVersions(
            dataset_versions=["dataset-1"],
            adapter_versions={"voc": "adapter-1"},
            manifest_versions={"voc": "manifest-1"},
            prompt_version="prompt-1",
            model_version="gemini-3.7-flash",
        ),
    )


def test_document_and_markdown_render_only_artifact_owned_facts() -> None:
    artifact = _artifact()
    replayed = RunArtifact.model_validate_json(artifact_json_bytes(artifact))

    document = render_document(replayed)
    markdown = render_markdown(replayed)

    assert document.headline == artifact.report.headline
    assert document.question == artifact.request.question
    assert document.scope.source_ids == artifact.request.enabled_sources
    assert document.goal == artifact.goal
    assert document.plan == artifact.plan
    assert document.notes == artifact.notes
    assert document.report == artifact.report
    assert document.provenance.result_ids == artifact_result_ids(artifact)
    assert document.provenance.fact_ids == [fact.fact_id for fact in artifact.facts]
    assert "Segment customers: 2 customers" in markdown
    assert "raw_fields" not in markdown
    assert "provider_response" not in markdown
    assert render_markdown_bytes(replayed) == f"{markdown}\n".encode()


@pytest.mark.parametrize("status", ["running", "awaiting_clarification", "degraded", "failed"])
def test_reportless_partial_degraded_and_failed_artifacts_render(status: str) -> None:
    terminal = status in {"degraded", "failed"}
    artifact = RunArtifact(
        run_id=RUN_ID,
        status=status,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW,
        completed_at=NOW if terminal else None,
        request=_request(),
        clarification=(
            ClarificationRecord(
                clarification_id="clarification-1",
                question="분석 기간을 확인해 주세요.",
            )
            if status == "awaiting_clarification"
            else None
        ),
        versions=RunVersions(),
        limitations=["완료된 보고서가 없습니다."],
        error=(
            PublicRunError(code="analysis_failed", message="공개 가능한 오류")
            if status == "failed"
            else None
        ),
    )

    document = render_document(artifact)
    markdown = render_markdown(artifact)

    assert document.report is None
    assert document.status == status
    assert status in markdown
    assert "완료된 보고서가 없습니다." in markdown
    if status == "failed":
        assert "analysis_failed" in markdown


def test_renderer_supports_legacy_report_without_changing_json_contract() -> None:
    artifact = _artifact()
    legacy = InsightReport(
        analysis_type="general",
        scope=AnalysisScope(
            start_at=SCOPE.start_at,
            end_at=SCOPE.end_at,
            enabled_sources=["voc"],
            population_description="선택 기간 고객",
        ),
        headline="Legacy 분석 결과",
        executive_summary="기존 보고서도 기록합니다.",
        metrics=[Metric(label="Customers", value=2, unit="customers", result_id="legacy:1")],
        sources_used=["voc"],
    )
    artifact = artifact.model_copy(update={"report": legacy})

    document = render_document(artifact)
    markdown = render_markdown(artifact)

    assert document.headline == "Legacy 분석 결과"
    assert "기존 보고서도 기록합니다." in markdown
    assert "Customers: 2 customers" in markdown


def test_markdown_escapes_html_and_markdown_control_text() -> None:
    artifact = _artifact()
    request = artifact.request.model_copy(
        update={"question": "<script>alert(1)</script>\n# forged heading"}
    )
    report = artifact.report.model_copy(update={"headline": '<img src=x onerror="alert(2)">'})
    artifact = artifact.model_copy(update={"request": request, "report": report})

    markdown = render_markdown(artifact)

    assert "<script>" not in markdown
    assert "<img" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "&lt;img" in markdown
    assert r"\# forged heading" in markdown
