"""Contracts for generic goals, plans, facts, notes, and reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from customer_signal.agent.contracts import RunRequest, SelectionContext
from customer_signal.agent.generic_fixture import (
    NEGATIVE_TOPIC_QUESTION,
    REPEAT_JOURNEY_QUESTION,
    SIGNUP_ABANDONMENT_QUESTION,
    GenericFixtureModel,
)
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    AnalysisStep,
    ClaimDraft,
    ContinueAfterStep,
    ContinueSelection,
    ExpectedOutputSpec,
    FactRef,
    GoalDecision,
    MeasureSpec,
    PopulationSpec,
    ReviseSelection,
    StopSelection,
    StopOnMetric,
    StepLimits,
    UnsupportedAnalysis,
)
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisFact,
    AnalysisJourneyEvent,
    AnalysisMetricDelta,
    AnalysisMetricFact,
    AnalysisQualityMetric,
    AnalysisRankedCustomer,
    AnalysisRepetitionMatch,
    AnalysisSequenceMatch,
    AnalysisSignal,
    CatalogSourcesPayload,
    CustomerJourneyPayload,
    CustomerRankingPayload,
    EvidencePayload,
    FactPayload,
    FactProvenance,
    FieldRef,
    ProfileEventsPayload,
    ProcessingStats,
    RepetitionPayload,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
    SequenceMatchPayload,
    build_fact,
    validate_comparison_payload,
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


def _bounded_plan_steps() -> list[AnalysisStep]:
    return [
        _step("step-catalog", "catalog_sources", CatalogSourcesInput(primitive="catalog_sources")),
        _step(
            "step-profile",
            "profile_events",
            ProfileEventsInput(primitive="profile_events"),
        ),
        _step(
            "step-segment",
            "segment_customers",
            SegmentCustomersInput(
                primitive="segment_customers",
                predicates=["outcome"],
                minimum_matching_events=1,
            ),
        ),
    ]


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


def test_plan_and_steps_publish_explanations_with_schema_v1_defaults() -> None:
    first, second, third = _bounded_plan_steps()

    explained_step = AnalysisStep.model_validate(
        {
            **first.model_dump(),
            "selection_reason": "  사용 가능한 Source를 먼저 확인합니다.  ",
        }
    )
    plan = AnalysisPlan(
        plan_id="plan-1",
        revision=0,
        goal_id="goal-1",
        steps=[explained_step, second, third],
        rationale="  요청 범위의 고객 Segment를 단계적으로 검증합니다.  ",
    )

    assert plan.steps[0].selection_reason == "사용 가능한 Source를 먼저 확인합니다."
    assert plan.rationale == "요청 범위의 고객 Segment를 단계적으로 검증합니다."
    assert second.selection_reason
    legacy_plan = plan.model_dump(exclude={"rationale"})
    legacy_plan["steps"] = [step.model_dump(exclude={"selection_reason"}) for step in plan.steps]
    restored = AnalysisPlan.model_validate(legacy_plan)
    assert restored.rationale
    assert all(step.selection_reason for step in restored.steps)


def test_step_selections_publish_bounded_public_reasons() -> None:
    first, second, third = _bounded_plan_steps()
    revised_plan = AnalysisPlan(
        plan_id="plan-revised",
        revision=1,
        goal_id="goal-1",
        steps=[first, second, third],
    )
    factories = [
        lambda reason=None: ContinueSelection(
            next_step_id="step-profile", **({} if reason is None else {"reason": reason})
        ),
        lambda reason=None: StopSelection(**({} if reason is None else {"reason": reason})),
        lambda reason=None: ReviseSelection(
            revised_plan=revised_plan,
            next_step_id="step-profile",
            **({} if reason is None else {"reason": reason}),
        ),
    ]

    for factory in factories:
        assert factory().reason
        assert factory("  검증 결과에 따라 다음 단계를 선택합니다.  ").reason == (
            "검증 결과에 따라 다음 단계를 선택합니다."
        )
        for invalid in ("", "   ", "가" * 501):
            with pytest.raises(ValidationError):
                factory(invalid)


@pytest.mark.parametrize(
    ("question", "expected_rationale", "expected_step_reasons", "expected_stop_reason"),
    [
        (
            NEGATIVE_TOPIC_QUESTION,
            "Source 범위와 이벤트 품질을 확인한 뒤 Topic별 부정 피드백 고객 규모를 집계합니다.",
            [
                "분석 가능한 Source와 요청 기간의 데이터 범위를 먼저 확인합니다.",
                "Topic별 집계 전에 이벤트 분포와 데이터 품질을 확인합니다.",
                "부정 결과를 Topic별로 집계해 관련 고객 규모를 확인합니다.",
            ],
            "Topic별 부정 피드백 집계를 완료해 분석을 종료합니다.",
        ),
        (
            REPEAT_JOURNEY_QUESTION,
            "Source를 확인한 뒤 반복 행동과 상담의 Sequence를 찾고, 대표 Journey와 마스킹된 근거를 순서대로 확인합니다.",
            [
                "분석 가능한 Source와 요청 기간의 데이터 범위를 먼저 확인합니다.",
                "반복 행동 뒤 상담으로 이어지는 Sequence 일치 고객을 찾습니다.",
                "Sequence 일치 고객의 대표 Journey를 확인합니다.",
                "대표 Journey를 뒷받침하는 마스킹된 근거를 확인합니다.",
            ],
            "Sequence, Journey, 근거 확인을 완료해 분석을 종료합니다.",
        ),
        (
            SIGNUP_ABANDONMENT_QUESTION,
            "Source를 확인한 뒤 가입 시작과 완료 Sequence를 분석하고, 완료하지 못한 고객 Segment를 확인합니다.",
            [
                "분석 가능한 Source와 요청 기간의 데이터 범위를 먼저 확인합니다.",
                "가입 시작과 완료 Sequence에서 완료하지 못한 고객 규모를 확인합니다.",
                "이탈 결과를 기준으로 가입을 완료하지 못한 고객 Segment를 구성합니다.",
            ],
            "미완료 고객 식별과 Segment 구성을 완료해 분석을 종료합니다.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_generic_fixture_publishes_scenario_specific_explanations(
    question: str,
    expected_rationale: str,
    expected_step_reasons: list[str],
    expected_stop_reason: str,
) -> None:
    model = GenericFixtureModel()
    goal = await model.create_goal(
        RunRequest(
            question=question,
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
            enabled_sources=["voc"],
        ),
        [],
    )
    assert isinstance(goal, AnalysisGoal)
    plan = await model.create_plan(goal, [])

    assert plan.rationale == expected_rationale
    assert [step.selection_reason for step in plan.steps] == expected_step_reasons
    for index, step in enumerate(plan.steps):
        selection = await model.select_next(
            SelectionContext(
                goal=goal,
                plan=plan,
                completed_step_ids=frozenset(completed.step_id for completed in plan.steps[:index]),
                facts=[],
            )
        )
        assert isinstance(selection, ContinueSelection)
        assert selection.next_step_id == step.step_id
        assert selection.reason == step.selection_reason

    stopped = await model.select_next(
        SelectionContext(
            goal=goal,
            plan=plan,
            completed_step_ids=frozenset(step.step_id for step in plan.steps),
            facts=[],
        )
    )
    assert isinstance(stopped, StopSelection)
    assert stopped.reason == expected_stop_reason


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
            requested_metric_key="event_count",
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
            requested_metric_key="event_count_delta",
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
            **{
                **common([zero("event_count_delta")], returned_rows=1),
                "metrics": [
                    AnalysisMetricFact(
                        metric_key="event_count_delta",
                        label="Event Count Delta",
                        value=0,
                        unit="events",
                    )
                ],
                "input_fact_ids": ["fact-a", "fact-b"],
            },
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


def test_comparison_binds_ordered_input_fact_ids_and_requested_delta_metric() -> None:
    values = {
        "kind": "compare_segments",
        "input_fact_ids": ["fact-x", "fact-y"],
        "processing": ProcessingStats(scanned_events=2, matched_events=2, returned_rows=1),
        "provenance": _payload().provenance,
        "metrics": [
            AnalysisMetricFact(
                metric_key="event_count_delta",
                label="Event count delta",
                value=1,
                unit="events",
            )
        ],
        "requested_metric_key": "event_count_delta",
        "baseline_fact_id": "fact-a",
        "comparison_fact_id": "fact-b",
        "deltas": [
            AnalysisMetricDelta(
                metric_key="event_count_delta",
                baseline=1,
                comparison=2,
                delta=1,
                unit="events",
            )
        ],
    }
    with pytest.raises(ValidationError, match="ordered input_fact_ids"):
        SegmentComparisonPayload.model_validate(values)

    values["input_fact_ids"] = ["fact-a", "fact-b"]
    values["metrics"] = [
        AnalysisMetricFact(
            metric_key="arbitrary_metric",
            label="Arbitrary",
            value=1,
            unit="events",
        )
    ]
    with pytest.raises(ValidationError, match="requested metric"):
        SegmentComparisonPayload.model_validate(values)


def test_comparison_delta_exactly_binds_ordered_input_fact_metrics() -> None:
    baseline = build_fact(
        fact_id="fact-a",
        step_id="step-baseline",
        primitive="segment_customers",
        result_id="result-a",
        payload=_payload(),
        scope=SCOPE,
        created_at=NOW,
    )
    comparison_payload = SegmentCustomersPayload(
        **{
            **_payload().model_dump(),
            "metrics": [_metric(3)],
            "customer_ids": ["customer-1", "customer-2", "customer-3"],
            "predicate_counts": {"outcome=negative": 3},
            "processing": ProcessingStats(scanned_events=5, matched_events=3, returned_rows=3),
        }
    )
    comparison = build_fact(
        fact_id="fact-b",
        step_id="step-comparison",
        primitive="segment_customers",
        result_id="result-b",
        payload=comparison_payload,
        scope=SCOPE,
        created_at=NOW,
    )
    payload = SegmentComparisonPayload(
        kind="compare_segments",
        input_fact_ids=["fact-a", "fact-b"],
        processing=ProcessingStats(scanned_events=2, matched_events=2, returned_rows=1),
        provenance=_payload().provenance,
        metrics=[
            AnalysisMetricFact(
                metric_key="segment_customer_count_delta",
                label="Segment customer delta",
                value=1,
                unit="customers",
            )
        ],
        requested_metric_key="segment_customer_count_delta",
        baseline_fact_id="fact-a",
        comparison_fact_id="fact-b",
        deltas=[
            AnalysisMetricDelta(
                metric_key="segment_customer_count",
                baseline=2,
                comparison=3,
                delta=1,
                unit="customers",
            )
        ],
    )
    validate_comparison_payload(payload, [baseline, comparison])
    with pytest.raises(ValueError, match="ordered input Facts"):
        build_fact(
            fact_id="fact-delta",
            step_id="step-compare",
            primitive="compare_segments",
            result_id="result-delta",
            payload=payload,
            scope=SCOPE,
            created_at=NOW,
        )
    built = build_fact(
        fact_id="fact-delta",
        step_id="step-compare",
        primitive="compare_segments",
        result_id="result-delta",
        payload=payload,
        scope=SCOPE,
        created_at=NOW,
        input_facts=[baseline, comparison],
    )
    assert built.metrics[0].value == 1

    forged = payload.model_copy(deep=True)
    forged.deltas[0] = AnalysisMetricDelta(
        metric_key="segment_customer_count",
        baseline=1,
        comparison=2,
        delta=1,
        unit="customers",
    )
    with pytest.raises(ValueError, match="input Fact metrics"):
        validate_comparison_payload(forged, [baseline, comparison])


def test_aggregate_requires_its_payload_owned_requested_metric_even_when_zero() -> None:
    common = {
        "kind": "aggregate_events",
        "input_fact_ids": [],
        "processing": ProcessingStats(scanned_events=0, matched_events=0, returned_rows=0),
        "provenance": _payload().provenance,
        "requested_metric_key": "conversion_rate",
        "buckets": [],
        "series": [],
    }
    with pytest.raises(ValidationError, match="requested metric"):
        AggregateEventsPayload(
            **common,
            metrics=[
                AnalysisMetricFact(
                    metric_key="arbitrary_metric",
                    label="Arbitrary",
                    value=0,
                    unit="rate",
                )
            ],
        )
    payload = AggregateEventsPayload(
        **common,
        metrics=[
            AnalysisMetricFact(
                metric_key="conversion_rate",
                label="Conversion rate",
                value=0,
                unit="rate",
            )
        ],
    )
    assert payload.metrics[0].value == 0


def test_fact_rejects_nested_field_source_outside_restricted_scope() -> None:
    payload = ProfileEventsPayload(
        kind="profile_events",
        input_fact_ids=[],
        processing=ProcessingStats(scanned_events=1, matched_events=1, returned_rows=1),
        provenance=_payload().provenance,
        metrics=[
            AnalysisMetricFact(
                metric_key="customer_count",
                label="Customers",
                value=1,
                unit="customers",
            ),
            AnalysisMetricFact(
                metric_key="event_count",
                label="Events",
                value=1,
                unit="events",
            ),
        ],
        distributions=[],
        data_quality=[
            AnalysisQualityMetric(
                field=FieldRef(field="billing_tier", field_kind="dimension", source_id="billing"),
                missing_count=0,
                total_count=1,
                missing_rate=0.0,
            )
        ],
    )
    with pytest.raises(ValidationError, match="nested source"):
        AnalysisFact(
            fact_id="fact-profile",
            step_id="step-profile",
            primitive="profile_events",
            result_id="result-profile",
            source_ids=["voc"],
            customer_ids=[],
            evidence_ids=[],
            metrics=payload.metrics,
            payload=payload,
            created_at=NOW,
        )

    scoped_metric = _metric().model_copy(update={"dimensions": {"billing.billing_tier": "premium"}})
    segment = SegmentCustomersPayload(
        **{
            **_payload().model_dump(),
            "metrics": [scoped_metric],
        }
    )
    with pytest.raises(ValidationError, match="nested source"):
        AnalysisFact(
            fact_id="fact-segment",
            step_id="step-segment",
            primitive="segment_customers",
            result_id="result-segment",
            source_ids=["voc"],
            customer_ids=segment.customer_ids,
            evidence_ids=[],
            metrics=segment.metrics,
            payload=segment,
            created_at=NOW,
        )

    ranking = CustomerRankingPayload(
        kind="rank_customers",
        input_fact_ids=["fact-segment"],
        processing=ProcessingStats(scanned_events=1, matched_events=1, returned_rows=1),
        provenance=_payload().provenance,
        metrics=[
            AnalysisMetricFact(
                metric_key="ranked_customer_count",
                label="Ranked customers",
                value=1,
                unit="customers",
            )
        ],
        customers=[
            AnalysisRankedCustomer(
                customer_id="customer-1",
                score=90,
                signals=[
                    AnalysisSignal(
                        signal_key="billing_risk",
                        label="Billing risk",
                        contribution=90,
                        metric_refs=["billing.risk_score"],
                        evidence_ids=[],
                    )
                ],
            )
        ],
    )
    with pytest.raises(ValidationError, match="nested source"):
        AnalysisFact(
            fact_id="fact-ranking",
            step_id="step-ranking",
            primitive="rank_customers",
            result_id="result-ranking",
            source_ids=["voc"],
            customer_ids=["customer-1"],
            evidence_ids=[],
            metrics=ranking.metrics,
            payload=ranking,
            created_at=NOW,
        )


def test_semantic_ids_are_unique_independent_of_sort_tuple() -> None:
    common = {
        "input_fact_ids": [],
        "processing": ProcessingStats(scanned_events=4, matched_events=2, returned_rows=2),
        "provenance": _payload().provenance,
    }
    with pytest.raises(ValidationError, match="customer"):
        RepetitionPayload(
            kind="detect_repetition",
            metrics=[
                AnalysisMetricFact(
                    metric_key="repeated_customer_count",
                    label="Repeated customers",
                    value=1,
                    unit="customers",
                )
            ],
            matches=[
                AnalysisRepetitionMatch(
                    customer_id="customer-1",
                    occurrence_count=3,
                    window=TimeRange(start_at=NOW, end_at=NOW + timedelta(hours=2)),
                    evidence_ids=["evidence-a"],
                ),
                AnalysisRepetitionMatch(
                    customer_id="customer-1",
                    occurrence_count=2,
                    window=TimeRange(start_at=NOW, end_at=NOW + timedelta(hours=1)),
                    evidence_ids=["evidence-b"],
                ),
            ],
            **common,
        )
    with pytest.raises(ValidationError, match="customer"):
        CustomerRankingPayload(
            kind="rank_customers",
            metrics=[
                AnalysisMetricFact(
                    metric_key="ranked_customer_count",
                    label="Ranked customers",
                    value=1,
                    unit="customers",
                )
            ],
            customers=[
                AnalysisRankedCustomer(customer_id="customer-1", score=90, signals=[]),
                AnalysisRankedCustomer(customer_id="customer-1", score=80, signals=[]),
            ],
            **common,
        )
    with pytest.raises(ValidationError, match="event_id"):
        CustomerJourneyPayload(
            kind="get_customer_journey",
            customer_id="customer-1",
            metrics=[
                AnalysisMetricFact(
                    metric_key="journey_event_count",
                    label="Journey events",
                    value=2,
                    unit="events",
                )
            ],
            events=[
                AnalysisJourneyEvent(
                    event_id="event-shared",
                    evidence_id="evidence-a",
                    source_id="voc",
                    occurred_at=NOW,
                    event_type="voc",
                    action="opened",
                    topic="pricing",
                    outcome="pending",
                    text="masked",
                ),
                AnalysisJourneyEvent(
                    event_id="event-shared",
                    evidence_id="evidence-b",
                    source_id="voc",
                    occurred_at=NOW + timedelta(minutes=1),
                    event_type="voc",
                    action="closed",
                    topic="pricing",
                    outcome="resolved",
                    text="masked",
                ),
            ],
            **common,
        )
    with pytest.raises(ValidationError, match="event_id"):
        SequenceMatchPayload(
            kind="match_sequence",
            matched_customer_ids=["customer-1", "customer-2"],
            metrics=[
                AnalysisMetricFact(
                    metric_key="matched_customer_count",
                    label="Matched customers",
                    value=2,
                    unit="customers",
                )
            ],
            matches=[
                AnalysisSequenceMatch(
                    customer_id="customer-1",
                    matched_event_ids=["event-shared", "event-a"],
                    window=TimeRange(start_at=NOW, end_at=NOW + timedelta(hours=1)),
                    evidence_ids=["evidence-a"],
                ),
                AnalysisSequenceMatch(
                    customer_id="customer-2",
                    matched_event_ids=["event-shared", "event-b"],
                    window=TimeRange(start_at=NOW, end_at=NOW + timedelta(hours=1)),
                    evidence_ids=["evidence-b"],
                ),
            ],
            **common,
        )


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
