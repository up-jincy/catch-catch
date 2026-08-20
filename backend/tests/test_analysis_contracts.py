"""Contracts for generic goals, plans, facts, notes, and reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    AnalysisStep,
    ClaimDraft,
    ContinueAfterStep,
    ExpectedOutputSpec,
    FactRef,
    GoalDecision,
    MeasureSpec,
    PopulationSpec,
    StopOnMetric,
    StepLimits,
    UnsupportedAnalysis,
)
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisFact,
    AnalysisMetricDelta,
    AnalysisMetricFact,
    CatalogSourcesPayload,
    CustomerJourneyPayload,
    CustomerRankingPayload,
    EvidencePayload,
    FactPayload,
    FactProvenance,
    ProfileEventsPayload,
    ProcessingStats,
    RepetitionPayload,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
    SequenceMatchPayload,
    build_fact,
)
from customer_signal.domain.primitives import (
    CatalogSourcesInput,
    ProfileEventsInput,
    SegmentCustomersInput,
)
from customer_signal.domain.reports import CustomerSignalReport, InsightReport, ReportContract
from customer_signal.domain.sources import EventScope, TimeRange


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)
SCOPE = EventScope(
    start_at=NOW,
    end_at=NOW + timedelta(days=1),
    source_ids=["voc"],
    max_events=100,
)


def _metric(value: int = 2) -> AnalysisMetricFact:
    return AnalysisMetricFact(
        metric_key="segment_customer_count",
        label="Segment customers",
        value=value,
        unit="customers",
    )


def _payload() -> SegmentCustomersPayload:
    return SegmentCustomersPayload(
        kind="segment_customers",
        input_fact_ids=[],
        processing=ProcessingStats(scanned_events=5, matched_events=2, returned_rows=2),
        provenance=FactProvenance(
            scope=SCOPE,
            source_ids=["voc"],
            adapter_versions={"voc": "1"},
            manifest_versions={"voc": "1"},
            dataset_version="test-1",
        ),
        metrics=[_metric()],
        segment_id="segment-negative",
        customer_ids=["customer-1", "customer-2"],
        predicate_counts={"outcome=negative": 2},
    )


def _step(
    step_id: str,
    primitive: str,
    parameters: object,
    *,
    inputs: list[str] | None = None,
) -> AnalysisStep:
    return AnalysisStep(
        step_id=step_id,
        primitive=primitive,
        parameters=parameters,
        source_ids=["voc"],
        input_step_ids=inputs or [],
        expected_output=ExpectedOutputSpec(
            payload_kind=primitive,
            required_metric_keys=[
                {
                    "catalog_sources": "source_count",
                    "profile_events": "event_count",
                    "segment_customers": "segment_customer_count",
                }.get(primitive, "ranked_customer_count")
            ],
        ),
        stop_condition=ContinueAfterStep(),
        limits=StepLimits(
            max_input_events=100,
            max_output_rows=20,
            max_evidence=5,
            timeout_seconds=5,
        ),
    )


def test_goal_decision_is_discriminated_and_strict() -> None:
    goal = AnalysisGoal(
        goal_id="goal-1",
        objective="Find negative-feedback customers",
        population=PopulationSpec(description="Customers with feedback"),
        time_range=TimeRange(start_at=NOW, end_at=NOW + timedelta(days=1)),
        source_ids=["voc"],
        measures=[
            MeasureSpec(
                metric_key="customer_count",
                label="Customers",
                aggregation="distinct_count",
                unit="customers",
            )
        ],
        output="segment",
    )
    assert TypeAdapter(GoalDecision).validate_python(goal.model_dump()) == goal

    with pytest.raises(ValidationError):
        AnalysisGoal.model_validate({**goal.model_dump(), "secret_reasoning": "hidden"})
    with pytest.raises(ValidationError):
        AnalysisGoal.model_validate(
            {
                **goal.model_dump(),
                "measures": [
                    {
                        **goal.measures[0].model_dump(),
                        "label": 123,
                    }
                ],
            }
        )


def test_plan_requires_three_to_six_unique_topological_steps() -> None:
    first = _step(
        "step-catalog", "catalog_sources", CatalogSourcesInput(primitive="catalog_sources")
    )
    second = _step(
        "step-profile",
        "profile_events",
        ProfileEventsInput(primitive="profile_events"),
    )
    third = _step(
        "step-segment",
        "segment_customers",
        SegmentCustomersInput(
            primitive="segment_customers", predicates=["outcome"], minimum_matching_events=1
        ),
    )
    plan = AnalysisPlan(
        plan_id="plan-1", revision=0, goal_id="goal-1", steps=[first, second, third]
    )
    assert len(plan.steps) == 3

    with pytest.raises(ValidationError):
        AnalysisPlan(plan_id="plan-1", revision=0, goal_id="goal-1", steps=[first])
    with pytest.raises(ValidationError, match="step_id values must be unique"):
        AnalysisPlan(plan_id="plan-1", revision=0, goal_id="goal-1", steps=[first, second, second])


def test_step_binds_primitive_parameters_expected_output_and_metric_stop() -> None:
    values = _step(
        "step-profile",
        "profile_events",
        ProfileEventsInput(primitive="profile_events"),
    ).model_dump()
    values["parameters"] = CatalogSourcesInput(primitive="catalog_sources").model_dump()
    with pytest.raises(ValidationError, match="parameters primitive"):
        AnalysisStep.model_validate(values)

    values = _step(
        "step-profile",
        "profile_events",
        ProfileEventsInput(primitive="profile_events"),
    ).model_dump()
    values["stop_condition"] = StopOnMetric(
        metric_key="customer_count", operator="gt", target=0
    ).model_dump()
    with pytest.raises(ValidationError, match="required metric"):
        AnalysisStep.model_validate(values)


def test_fact_builder_projects_exact_server_authorization() -> None:
    fact = build_fact(
        fact_id="fact-1",
        step_id="step-segment",
        primitive="segment_customers",
        result_id="result-1",
        payload=_payload(),
        scope=SCOPE,
        created_at=NOW,
    )
    assert fact.customer_ids == ["customer-1", "customer-2"]
    assert fact.evidence_ids == []
    assert fact.metrics == [_metric()]
    assert fact.source_ids == ["voc"]

    forged = fact.model_dump()
    forged["customer_ids"] = ["customer-forged"]
    with pytest.raises(ValidationError, match="payload projection"):
        AnalysisFact.model_validate(forged)


def test_fact_rejects_wrong_restricted_source_and_metric_projection() -> None:
    payload = _payload()
    values = {
        "fact_id": "fact-1",
        "step_id": "step-segment",
        "primitive": "segment_customers",
        "result_id": "result-1",
        "source_ids": ["search_history"],
        "customer_ids": payload.customer_ids,
        "evidence_ids": [],
        "metrics": payload.metrics,
        "payload": payload,
        "created_at": NOW,
    }
    with pytest.raises(ValidationError, match="restricted scope"):
        AnalysisFact.model_validate(values)

    values["source_ids"] = ["voc"]
    values["metrics"] = [_metric(3)]
    with pytest.raises(ValidationError, match="payload projection"):
        AnalysisFact.model_validate(values)


def test_fact_payload_keeps_zero_result_canonical_metric() -> None:
    values = _payload().model_dump()
    values["customer_ids"] = []
    values["processing"] = {"scanned_events": 5, "matched_events": 0, "returned_rows": 0}
    values["metrics"] = []
    with pytest.raises(ValidationError, match="metrics"):
        SegmentCustomersPayload.model_validate(values)

    values["metrics"] = [_metric(0).model_dump()]
    assert SegmentCustomersPayload.model_validate(values).metrics[0].value == 0


def test_note_draft_cannot_claim_server_owned_facts() -> None:
    draft = AnalysisNoteDraft(
        step_id="step-segment",
        claims=[
            ClaimDraft(
                claim_type="metric",
                subject="segment_customer_count",
                operator="eq",
                target=2,
                fact_refs=[
                    FactRef(
                        fact_id="fact-1",
                        metric_key="segment_customer_count",
                        plan_revision=0,
                    )
                ],
            )
        ],
        next_step_id=None,
    )
    with pytest.raises(ValidationError):
        AnalysisNoteDraft.model_validate({**draft.model_dump(), "facts": [{"fact_id": "forged"}]})


def test_all_ten_payloads_are_discriminated_and_require_common_server_fields() -> None:
    def common(metrics: list[AnalysisMetricFact], *, returned_rows: int = 0) -> dict[str, object]:
        return {
            "input_fact_ids": [],
            "processing": ProcessingStats(
                scanned_events=0, matched_events=0, returned_rows=returned_rows
            ),
            "provenance": FactProvenance(
                scope=SCOPE,
                source_ids=["voc"],
                adapter_versions={"voc": "1"},
                manifest_versions={"voc": "1"},
                dataset_version="test-1",
            ),
            "metrics": metrics,
        }

    def zero(metric_key: str) -> AnalysisMetricFact:
        return AnalysisMetricFact(
            metric_key=metric_key,
            label=metric_key.replace("_", " ").title(),
            value=0,
            unit="count",
        )

    payloads: list[FactPayload] = [
        CatalogSourcesPayload(kind="catalog_sources", sources=[], **common([zero("source_count")])),
        ProfileEventsPayload(
            kind="profile_events",
            distributions=[],
            data_quality=[],
            **common([zero("customer_count"), zero("event_count")]),
        ),
        AggregateEventsPayload(
            kind="aggregate_events",
            buckets=[],
            series=[],
            **common([zero("event_count")]),
        ),
        SegmentCustomersPayload(
            kind="segment_customers",
            segment_id="empty-segment",
            customer_ids=[],
            predicate_counts={},
            **common([zero("segment_customer_count")]),
        ),
        RepetitionPayload(
            kind="detect_repetition",
            matches=[],
            **common([zero("repeated_customer_count")]),
        ),
        SequenceMatchPayload(
            kind="match_sequence",
            matched_customer_ids=[],
            matches=[],
            **common([zero("matched_customer_count")]),
        ),
        SegmentComparisonPayload(
            kind="compare_segments",
            baseline_fact_id="fact-a",
            comparison_fact_id="fact-b",
            deltas=[
                AnalysisMetricDelta(
                    metric_key="event_count",
                    baseline=0,
                    comparison=0,
                    delta=0,
                    unit="events",
                )
            ],
            **common([zero("event_count_delta")], returned_rows=1),
        ),
        CustomerRankingPayload(
            kind="rank_customers",
            customers=[],
            **common([zero("ranked_customer_count")]),
        ),
        CustomerJourneyPayload(
            kind="get_customer_journey",
            customer_id="customer-selected",
            events=[],
            **common([zero("journey_event_count")]),
        ),
        EvidencePayload(
            kind="get_evidence",
            records=[],
            **common([zero("evidence_record_count")]),
        ),
    ]
    adapter = TypeAdapter(FactPayload)
    assert [adapter.validate_python(payload.model_dump()).kind for payload in payloads] == [
        "catalog_sources",
        "profile_events",
        "aggregate_events",
        "segment_customers",
        "detect_repetition",
        "match_sequence",
        "compare_segments",
        "rank_customers",
        "get_customer_journey",
        "get_evidence",
    ]
    with pytest.raises(ValidationError):
        adapter.validate_python({**payloads[-1].model_dump(), "raw_fields": {"email": "x"}})


def test_unsupported_sensitive_requests_remain_non_executable_goal_decisions() -> None:
    decision = TypeAdapter(GoalDecision).validate_python(
        UnsupportedAnalysis(
            code="raw_export",
            reason="Raw records cannot be exported.",
            suggested_questions=["Show masked evidence for verified signals."],
        ).model_dump()
    )
    assert isinstance(decision, UnsupportedAnalysis)
    assert not isinstance(decision, AnalysisGoal)


def test_generic_and_legacy_reports_have_distinct_defaulted_discriminators() -> None:
    assert InsightReport.model_fields["report_kind"].default == "legacy_journey"
    assert CustomerSignalReport.model_fields["report_kind"].default == "customer_signal"
    schema = TypeAdapter(ReportContract).json_schema()
    assert schema["discriminator"] == {
        "mapping": {
            "customer_signal": "#/$defs/CustomerSignalReport",
            "legacy_journey": "#/$defs/InsightReport",
        },
        "propertyName": "report_kind",
    }
