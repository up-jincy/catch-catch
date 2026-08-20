"""Explicit deterministic model drafts for the three generic demo questions."""

from __future__ import annotations

from customer_signal.agent.contracts import (
    ReportModelContext,
    RunRequest,
    SelectionContext,
    StepModelContext,
)
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    AnalysisStep,
    ClaimDraft,
    ClarificationRequired,
    ContinueAfterStep,
    ContinueSelection,
    CustomerSignalReportDraft,
    ExpectedOutputSpec,
    FactRef,
    MeasureSpec,
    PopulationSpec,
    RecommendedActionDraft,
    SequenceSpec,
    StepLimits,
    StopSelection,
    UnsupportedAnalysis,
)
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    GetCustomerJourneyInput,
    GetEvidenceInput,
    MatchSequenceInput,
    ProfileEventsInput,
    SegmentCustomersInput,
)
from customer_signal.domain.sources import SourceManifest, TimeRange


NEGATIVE_TOPIC_QUESTION = "최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘."
REPEAT_JOURNEY_QUESTION = "반복 행동 뒤 상담으로 전환되는 Journey를 보여줘."
SIGNUP_ABANDONMENT_QUESTION = "가입 시작 뒤 완료하지 못한 고객과 이탈 단계를 알려줘."
AMBIGUOUS_QUESTION = "최근 고객 신호를 분석해줘."

_LIMITS = StepLimits(
    max_input_events=10_000,
    max_output_rows=100,
    max_evidence=20,
    timeout_seconds=40.0,
)
_SUGGESTIONS = [
    NEGATIVE_TOPIC_QUESTION,
    REPEAT_JOURNEY_QUESTION,
    SIGNUP_ABANDONMENT_QUESTION,
]


class GenericFixtureModel:
    """Produce only typed planning/draft values; numeric truth always comes from Facts."""

    agent_mode = "fixture"
    model_name: str | None = None

    async def create_goal(
        self,
        request: RunRequest,
        manifests: list[SourceManifest],
    ):
        del manifests
        scenario = _scenario(request.question)
        if scenario == "clarification":
            return ClarificationRequired(
                clarification_id="clarification-analysis-scope",
                question="어떤 고객 신호와 결과 형태를 분석할지 구체적으로 알려주세요.",
            )
        if scenario == "unsafe":
            return UnsupportedAnalysis(
                code="pii_request",
                reason="원본 개인정보, Raw export 또는 쓰기 작업은 지원하지 않습니다.",
                suggested_questions=_SUGGESTIONS,
            )
        if scenario == "unsupported":
            return UnsupportedAnalysis(
                code="unsupported_statistic",
                reason="현재 데모가 검증할 수 없는 분석 요청입니다.",
                suggested_questions=_SUGGESTIONS,
            )

        metric_key, label, output = {
            "negative": (
                "negative_feedback_customer_count",
                "Negative Feedback Customers",
                "aggregate",
            ),
            "repeat": ("matched_customer_count", "Matched Customers", "journey"),
            "signup": ("abandoned_customer_count", "Abandoned Customers", "segment"),
        }[scenario]
        return AnalysisGoal(
            goal_id=f"goal-{scenario}",
            objective={
                "negative": "부정 피드백이 집중된 Topic과 고객 규모를 확인합니다.",
                "repeat": "반복 행동 뒤 상담으로 이어진 고객 Journey를 확인합니다.",
                "signup": "가입을 시작했지만 완료하지 않은 고객과 이탈 단계를 확인합니다.",
            }[scenario],
            population=PopulationSpec(description="요청 기간과 Source 범위의 고객"),
            time_range=TimeRange(start_at=request.start_at, end_at=request.end_at),
            source_ids=list(request.enabled_sources),
            measures=[
                MeasureSpec(
                    metric_key=metric_key,
                    label=label,
                    aggregation="distinct_count",
                    unit="customers",
                )
            ],
            sequence=(
                SequenceSpec(
                    steps=(
                        ["repeat_behavior", "support_contact"]
                        if scenario == "repeat"
                        else ["signup_started", "signup_completed"]
                    ),
                    within_hours=24 * 30,
                )
                if scenario in {"repeat", "signup"}
                else None
            ),
            output=output,
        )

    async def create_plan(
        self,
        goal: AnalysisGoal,
        manifests: list[SourceManifest],
    ) -> AnalysisPlan:
        del manifests
        scenario = goal.goal_id.removeprefix("goal-")
        steps = [_catalog_step(goal)]
        if scenario == "negative":
            steps.extend(
                [
                    _step(
                        step_id="step-profile",
                        primitive="profile_events",
                        parameters=ProfileEventsInput(primitive="profile_events"),
                        source_ids=goal.source_ids,
                        required_metrics=["customer_count", "event_count"],
                    ),
                    _step(
                        step_id="step-negative-topic",
                        primitive="aggregate_events",
                        parameters=AggregateEventsInput(
                            primitive="aggregate_events",
                            aggregation="count",
                            group_by=["topic"],
                            predicates=["outcome == 'negative'"],
                            time_grain="day",
                        ),
                        source_ids=goal.source_ids,
                        required_metrics=["negative_feedback_customer_count"],
                    ),
                ]
            )
        elif scenario == "repeat":
            steps.extend(
                [
                    _step(
                        step_id="step-repeat-sequence",
                        primitive="match_sequence",
                        parameters=MatchSequenceInput(
                            primitive="match_sequence",
                            sequence=["repeat_behavior", "support_contact"],
                        ),
                        source_ids=goal.source_ids,
                        required_metrics=["matched_customer_count"],
                    ),
                    _step(
                        step_id="step-repeat-journey",
                        primitive="get_customer_journey",
                        parameters=GetCustomerJourneyInput(
                            primitive="get_customer_journey", limit=20
                        ),
                        source_ids=goal.source_ids,
                        input_step_ids=["step-repeat-sequence"],
                        required_metrics=["journey_event_count"],
                    ),
                    _step(
                        step_id="step-repeat-evidence",
                        primitive="get_evidence",
                        parameters=GetEvidenceInput(primitive="get_evidence", limit=20),
                        source_ids=goal.source_ids,
                        input_step_ids=["step-repeat-journey"],
                        required_metrics=["evidence_record_count"],
                    ),
                ]
            )
        elif scenario == "signup":
            steps.extend(
                [
                    _step(
                        step_id="step-signup-sequence",
                        primitive="match_sequence",
                        parameters=MatchSequenceInput(
                            primitive="match_sequence",
                            sequence=["signup_started", "signup_completed"],
                        ),
                        source_ids=goal.source_ids,
                        required_metrics=[
                            "abandoned_customer_count",
                            "matched_customer_count",
                        ],
                    ),
                    _step(
                        step_id="step-signup-segment",
                        primitive="segment_customers",
                        parameters=SegmentCustomersInput(
                            primitive="segment_customers",
                            predicates=["outcome == 'abandoned'"],
                            minimum_matching_events=1,
                        ),
                        source_ids=goal.source_ids,
                        required_metrics=["segment_customer_count"],
                    ),
                ]
            )
        else:
            raise ValueError("Fixture Goal does not map to a supported Plan")
        return AnalysisPlan(
            plan_id=f"plan-{scenario}",
            revision=0,
            goal_id=goal.goal_id,
            steps=steps,
        )

    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft:
        metric = next(
            (
                context.current_fact.metric(metric_key)
                for metric_key in context.step.expected_output.required_metric_keys
                if any(
                    fact_metric.metric_key == metric_key
                    for fact_metric in context.current_fact.metrics
                )
            ),
            context.current_fact.metrics[0],
        )
        return AnalysisNoteDraft(
            step_id=context.step.step_id,
            claims=[
                ClaimDraft(
                    claim_type="metric",
                    subject=metric.metric_key,
                    operator="eq",
                    target=metric.value,
                    fact_refs=[
                        FactRef(
                            fact_id=context.current_fact.fact_id,
                            result_id=context.current_fact.result_id,
                            metric_key=metric.metric_key,
                            label=metric.label,
                            unit=metric.unit,
                            dimensions=metric.dimensions,
                            plan_revision=context.plan.revision,
                        )
                    ],
                )
            ],
            next_step_id=None,
            limitations=[],
        )

    async def select_next(self, context: SelectionContext):
        next_step = next(
            (step for step in context.plan.steps if step.step_id not in context.completed_step_ids),
            None,
        )
        if next_step is None:
            return StopSelection()
        return ContinueSelection(next_step_id=next_step.step_id)

    async def create_report(self, context: ReportModelContext) -> CustomerSignalReportDraft:
        claims = [claim for note in context.notes for claim in note.claims]
        claim_ids = [claim.claim_id for claim in claims]
        actions = []
        if claims:
            last_claim = claims[-1]
            fact_ids = list(dict.fromkeys(reference.fact_id for reference in last_claim.fact_refs))
            actions = [
                RecommendedActionDraft(
                    action_id={
                        "aggregate": "further_analysis",
                        "journey": "journey_improvement",
                        "segment": "customer_followup",
                    }[context.goal.output],
                    claim_refs=[last_claim.claim_id],
                    fact_refs=fact_ids,
                )
            ]
        return CustomerSignalReportDraft(
            goal_id=context.goal.goal_id,
            claim_refs=claim_ids,
            recommended_actions=actions,
        )


def _catalog_step(goal: AnalysisGoal) -> AnalysisStep:
    return _step(
        step_id="step-catalog",
        primitive="catalog_sources",
        parameters=CatalogSourcesInput(primitive="catalog_sources"),
        source_ids=goal.source_ids,
        required_metrics=["source_count"],
    )


def _step(
    *,
    step_id,
    primitive,
    parameters,
    source_ids,
    required_metrics,
    input_step_ids=None,
) -> AnalysisStep:
    return AnalysisStep(
        step_id=step_id,
        primitive=primitive,
        parameters=parameters,
        source_ids=list(source_ids),
        input_step_ids=list(input_step_ids or []),
        expected_output=ExpectedOutputSpec(
            payload_kind=primitive,
            required_metric_keys=list(required_metrics),
        ),
        stop_condition=ContinueAfterStep(),
        limits=_LIMITS,
    )


def _scenario(question: str) -> str:
    normalized = " ".join(question.casefold().split())
    if normalized == " ".join(AMBIGUOUS_QUESTION.casefold().split()):
        return "clarification"
    if any(
        token in normalized
        for token in (
            "이메일",
            "전화번호",
            "개인정보",
            "원본",
            "raw",
            "export",
            "삭제",
            "쓰기",
        )
    ):
        return "unsafe"
    if "부정" in normalized and "피드백" in normalized:
        return "negative"
    if "반복" in normalized and ("상담" in normalized or "journey" in normalized):
        return "repeat"
    if "가입" in normalized and ("완료" in normalized or "이탈" in normalized):
        return "signup"
    return "unsupported"


__all__ = [
    "AMBIGUOUS_QUESTION",
    "GenericFixtureModel",
    "NEGATIVE_TOPIC_QUESTION",
    "REPEAT_JOURNEY_QUESTION",
    "SIGNUP_ABANDONMENT_QUESTION",
]
