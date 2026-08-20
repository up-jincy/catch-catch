"""Functional generic Analysis Loop acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from customer_signal.agent.analysis_loop import AnalysisLoop
from customer_signal.agent.contracts import GenericRunnerOutcome, RunRequest
from customer_signal.agent.generic_fixture import (
    AMBIGUOUS_QUESTION,
    NEGATIVE_TOPIC_QUESTION,
    REPEAT_JOURNEY_QUESTION,
    SIGNUP_ABANDONMENT_QUESTION,
    GenericFixtureModel,
)
from customer_signal.domain.analysis import (
    AnalysisPlan,
    CustomerSignalReportDraft,
    ReviseSelection,
    StopSelection,
)
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisMetricFact,
    AnalysisSourceCatalogFact,
    CatalogSourcesPayload,
    CustomerJourneyPayload,
    EvidencePayload,
    FactProvenance,
    ProcessingStats,
    ProfileEventsPayload,
    SegmentCustomersPayload,
    SequenceMatchPayload,
    build_fact,
)
from customer_signal.domain.sources import (
    EventScope,
    IdentityQualityDescriptor,
    MaskingPolicy,
    SourceManifest,
    TimeRange,
)
from customer_signal.domain.types import GenericPrimitiveName


NOW = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
ALL_PRIMITIVES: frozenset[GenericPrimitiveName] = frozenset(
    {
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
    }
)


def _request(question: str, *, start_at: datetime | None = None) -> RunRequest:
    return RunRequest(
        question=question,
        start_at=start_at or NOW - timedelta(days=30),
        end_at=NOW,
        enabled_sources=["voc"],
    )


def _manifest() -> SourceManifest:
    return SourceManifest(
        source_id="voc",
        label="VOC",
        description="Masked customer support signals",
        adapter_version="adapter-1",
        manifest_version="manifest-1",
        data_interval=TimeRange(start_at=NOW - timedelta(days=365), end_at=NOW + timedelta(days=1)),
        refresh_cadence="static_demo",
        supported_event_types=frozenset({"voc"}),
        supported_topics=frozenset({"quality", "signup"}),
        supported_outcomes=frozenset({"negative", "pending", "abandoned"}),
        dimensions={},
        measures={},
        capabilities=ALL_PRIMITIVES,
        masking_policy=MaskingPolicy(rules={}),
        identity_quality=IdentityQualityDescriptor(
            namespace="voc-user",
            link_method="synthetic",
            confidence=1.0,
        ),
    )


class NoDataScope(RuntimeError):
    code = "no_data_scope"


class ScriptedExecutor:
    def __init__(self, *, no_data: bool = False, fail_after: int | None = None) -> None:
        self.no_data = no_data
        self.fail_after = fail_after
        self.calls: list[str] = []

    async def execute_async(
        self,
        step,
        *,
        scope: EventScope,
        prior_facts: list,
        budget: Any,
    ):
        del budget
        if self.no_data:
            raise NoDataScope("no rows")
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("private executor failure")
        self.calls.append(step.step_id)
        input_facts = [
            next(fact for fact in prior_facts if fact.step_id == dependency)
            for dependency in step.input_step_ids
        ]
        payload = self._payload(step, scope, input_facts)
        return build_fact(
            fact_id=f"fact-{step.step_id}",
            step_id=step.step_id,
            primitive=step.primitive,
            result_id=f"{step.primitive}:{step.step_id}",
            payload=payload,
            scope=scope,
            created_at=NOW,
        )

    def _common(self, scope: EventScope, *, returned_rows: int, input_facts: list) -> dict:
        return {
            "input_fact_ids": [fact.fact_id for fact in input_facts],
            "processing": ProcessingStats(
                scanned_events=20,
                matched_events=min(returned_rows, 20),
                returned_rows=returned_rows,
            ),
            "provenance": FactProvenance(
                scope=scope,
                source_ids=list(scope.source_ids),
                adapter_versions={source_id: "adapter-1" for source_id in scope.source_ids},
                manifest_versions={source_id: "manifest-1" for source_id in scope.source_ids},
                dataset_version="dataset-acceptance",
            ),
        }

    @staticmethod
    def _metric(key: str, value: int, unit: str = "customers") -> AnalysisMetricFact:
        return AnalysisMetricFact(
            metric_key=key,
            label=key.replace("_", " ").title(),
            value=value,
            unit=unit,
        )

    def _payload(self, step, scope: EventScope, input_facts: list):
        common = self._common(scope, returned_rows=1, input_facts=input_facts)
        if step.primitive == "catalog_sources":
            return CatalogSourcesPayload(
                kind="catalog_sources",
                metrics=[self._metric("source_count", len(scope.source_ids), "sources")],
                sources=[
                    AnalysisSourceCatalogFact(
                        source_id=source_id,
                        data_interval=TimeRange(start_at=scope.start_at, end_at=scope.end_at),
                        row_count=20,
                        manifest_version="manifest-1",
                    )
                    for source_id in scope.source_ids
                ],
                **common,
            )
        if step.primitive == "profile_events":
            return ProfileEventsPayload(
                kind="profile_events",
                metrics=[
                    self._metric("customer_count", 12),
                    self._metric("event_count", 20, "events"),
                ],
                distributions=[],
                data_quality=[],
                **common,
            )
        if step.primitive == "aggregate_events":
            metric_key = step.expected_output.required_metric_keys[0]
            return AggregateEventsPayload(
                kind="aggregate_events",
                requested_metric_key=metric_key,
                metrics=[self._metric(metric_key, 6)],
                buckets=[],
                series=[],
                **common,
            )
        if step.primitive == "match_sequence":
            is_signup = "abandoned_customer_count" in step.expected_output.required_metric_keys
            value = 5 if is_signup else 6
            metrics = [self._metric("matched_customer_count", value)]
            if is_signup:
                metrics.insert(0, self._metric("abandoned_customer_count", value))
            return SequenceMatchPayload(
                kind="match_sequence",
                metrics=metrics,
                matched_customer_ids=[],
                matches=[],
                **common,
            )
        if step.primitive == "segment_customers":
            customer_ids = [f"customer-{index:03d}" for index in range(1, 6)]
            return SegmentCustomersPayload(
                kind="segment_customers",
                metrics=[self._metric("segment_customer_count", 5)],
                segment_id="signup-abandoned",
                customer_ids=customer_ids,
                predicate_counts={"signup_started_not_completed": 5},
                **self._common(scope, returned_rows=5, input_facts=input_facts),
            )
        if step.primitive == "get_customer_journey":
            return CustomerJourneyPayload(
                kind="get_customer_journey",
                metrics=[self._metric("journey_event_count", 1, "events")],
                customer_id="customer-001",
                events=[],
                **common,
            )
        if step.primitive == "get_evidence":
            return EvidencePayload(
                kind="get_evidence",
                metrics=[self._metric("evidence_record_count", 0, "records")],
                records=[],
                **self._common(scope, returned_rows=0, input_facts=input_facts),
            )
        raise AssertionError(f"unexpected primitive: {step.primitive}")


class RepairingPlanModel(GenericFixtureModel):
    def __init__(self, *, invalid_attempts: int) -> None:
        self.invalid_attempts = invalid_attempts
        self.validation_feedback: list[str | None] = []
        self.accepted_plan: AnalysisPlan | None = None

    async def create_plan(
        self,
        goal,
        manifests,
        *,
        validation_feedback: str | None = None,
    ) -> AnalysisPlan:
        self.validation_feedback.append(validation_feedback)
        plan = await super().create_plan(goal, manifests)
        self.accepted_plan = plan
        if len(self.validation_feedback) <= self.invalid_attempts:
            invalid_first_step = plan.steps[0].model_copy(
                update={"source_ids": ["unknown_v2"]}
            )
            return plan.model_copy(update={"steps": [invalid_first_step, *plan.steps[1:]]})
        return plan


class CatalogFactRevisionModel(GenericFixtureModel):
    revision_reason = (
        "Catalog Fact에서 확인한 데이터 범위에 맞춰 새 Profile 단계를 실행합니다."
    )
    revised_rationale = (
        "Catalog Fact에서 VOC Source를 확인해 새 Profile 단계를 Plan에 반영합니다."
    )
    stop_reason = "새 Profile Fact로 필요한 범위를 확인해 분석을 종료합니다."

    def __init__(self) -> None:
        self.initial_plan: AnalysisPlan | None = None

    async def create_plan(
        self,
        goal,
        manifests,
        *,
        validation_feedback: str | None = None,
    ) -> AnalysisPlan:
        del validation_feedback
        self.initial_plan = await super().create_plan(goal, manifests)
        return self.initial_plan

    async def select_next(self, context):
        if context.plan.revision == 0:
            adaptive_step = context.plan.steps[1].model_copy(
                update={
                    "step_id": "step-catalog-profile",
                    "selection_reason": self.revision_reason,
                }
            )
            revised_plan = context.plan.model_copy(
                update={
                    "revision": context.plan.revision + 1,
                    "steps": [
                        context.plan.steps[0],
                        adaptive_step,
                        context.plan.steps[2],
                    ],
                    "rationale": self.revised_rationale,
                }
            )
            return ReviseSelection(
                revised_plan=revised_plan,
                next_step_id=adaptive_step.step_id,
                reason=self.revision_reason,
            )
        return StopSelection(reason=self.stop_reason)


@pytest.mark.asyncio
async def test_invalid_initial_plan_is_rewritten_once_before_execution() -> None:
    model = RepairingPlanModel(invalid_attempts=1)
    executor = ScriptedExecutor()
    loop = AnalysisLoop(model=model, executor=executor, manifests=[_manifest()])
    events = []

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=events.append)

    assert outcome.status == "completed"
    assert model.accepted_plan is not None
    assert len(model.validation_feedback) == 2
    assert model.validation_feedback[0] is None
    feedback = model.validation_feedback[1]
    assert feedback is not None
    assert 1 <= len(feedback) <= 500
    assert "unknown or disabled source" in feedback.casefold()
    assert executor.calls == [step.step_id for step in model.accepted_plan.steps]
    assert [event.type for event in events].count("plan_created") == 1


@pytest.mark.asyncio
async def test_second_invalid_plan_fails_with_goal_without_executing_a_primitive() -> None:
    model = RepairingPlanModel(invalid_attempts=2)
    executor = ScriptedExecutor()
    loop = AnalysisLoop(model=model, executor=executor, manifests=[_manifest()])
    events = []

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=events.append)

    assert outcome.status == "failed"
    assert outcome.goal is not None
    assert outcome.goal.goal_id == "goal-negative"
    assert outcome.plan is None
    assert len(model.validation_feedback) == 2
    assert executor.calls == []
    assert all(event.type != "plan_created" for event in events)


@pytest.mark.asyncio
async def test_catalog_fact_revises_unfinished_plan_before_publishing_next_action() -> None:
    model = CatalogFactRevisionModel()
    executor = ScriptedExecutor()
    loop = AnalysisLoop(model=model, executor=executor, manifests=[_manifest()])
    events = []

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=events.append)

    assert outcome.status == "completed"
    assert outcome.plan is not None
    assert outcome.plan.revision == 1
    assert model.initial_plan is not None
    assert executor.calls == ["step-catalog", "step-catalog-profile"]
    revised_events = [event for event in events if event.type == "plan_revised"]
    assert len(revised_events) == 1
    revised_plan = revised_events[0].payload["plan"]
    assert revised_plan["revision"] == 1
    assert revised_plan["rationale"] == model.revised_rationale
    assert revised_plan["steps"][0] == model.initial_plan.steps[0].model_dump(mode="json")
    first_note = next(event for event in events if event.type == "analysis_note_created")
    assert first_note.payload["note"]["next_step_id"] == "step-catalog-profile"
    assert first_note.payload["note"]["next_action"] == model.revision_reason
    assert first_note.payload["note"]["plan_revision"] == 0
    event_types = [event.type for event in events]
    assert event_types.index("step_completed") < event_types.index("plan_revised")


@pytest.mark.parametrize(
    ("question", "expected_primitive", "metric_key", "value"),
    [
        (NEGATIVE_TOPIC_QUESTION, "aggregate_events", "negative_feedback_customer_count", 6),
        (REPEAT_JOURNEY_QUESTION, "match_sequence", "matched_customer_count", 6),
        (SIGNUP_ABANDONMENT_QUESTION, "match_sequence", "abandoned_customer_count", 5),
    ],
)
@pytest.mark.asyncio
async def test_loop_executes_three_distinct_fact_backed_demo_questions(
    question: str,
    expected_primitive: str,
    metric_key: str,
    value: int,
) -> None:
    executor = ScriptedExecutor()
    loop = AnalysisLoop(
        model=GenericFixtureModel(),
        executor=executor,
        manifests=[_manifest()],
        max_events=100,
    )
    events = []

    outcome = await loop.run(_request(question), emit=events.append)

    assert outcome.status == "completed"
    assert outcome.report is not None
    assert expected_primitive in [fact.primitive for fact in outcome.facts]
    metric = next(metric for metric in outcome.report.metrics if metric.metric_key == metric_key)
    assert metric.value == value
    assert outcome.notes
    assert all(note.fact_ids and note.claims for note in outcome.notes)
    assert outcome.report.findings
    assert outcome.report.provenance.result_ids == [fact.result_id for fact in outcome.facts]
    event_types = [event.type for event in events]
    assert event_types[:2] == ["goal_created", "plan_created"]
    assert "fact_created" in event_types
    assert "analysis_note_created" in event_types
    assert event_types[-2:] == ["report_validating", "result"]


@pytest.mark.asyncio
async def test_loop_emits_task9_full_public_payload_envelopes() -> None:
    loop = AnalysisLoop(
        model=GenericFixtureModel(),
        executor=ScriptedExecutor(),
        manifests=[_manifest()],
    )
    events = []

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=events.append)

    assert outcome.report is not None
    goal_event = next(event for event in events if event.type == "goal_created")
    plan_event = next(event for event in events if event.type == "plan_created")
    started_event = next(event for event in events if event.type == "step_started")
    fact_event = next(event for event in events if event.type == "fact_created")
    note_event = next(event for event in events if event.type == "analysis_note_created")
    completed_event = next(event for event in events if event.type == "step_completed")
    validating_event = next(event for event in events if event.type == "report_validating")
    result_event = next(event for event in events if event.type == "result")

    assert goal_event.payload == {"goal": outcome.goal.model_dump(mode="json")}
    assert plan_event.payload == {"plan": outcome.plan.model_dump(mode="json")}
    started_step = next(
        step for step in outcome.plan.steps if step.step_id == started_event.payload["step_id"]
    )
    assert started_event.payload["selection_reason"] == started_step.selection_reason
    assert fact_event.payload == {
        "step_id": outcome.facts[0].step_id,
        "fact": outcome.facts[0].model_dump(mode="json"),
    }
    assert note_event.payload == {"note": outcome.notes[0].model_dump(mode="json")}
    assert completed_event.payload == {
        "step_id": outcome.facts[0].step_id,
        "status": "completed",
        "result_ids": [outcome.facts[0].result_id],
        "duration_ms": outcome.notes[0].duration_ms,
    }
    assert validating_event.payload == {
        "fact_ids": [fact.fact_id for fact in outcome.facts],
        "result_ids": [fact.result_id for fact in outcome.facts],
    }
    assert result_event.payload == {
        "agent_mode": "fixture",
        "report": outcome.report.model_dump(mode="json"),
    }


@pytest.mark.asyncio
async def test_clarification_and_unsupported_questions_never_execute_primitives() -> None:
    executor = ScriptedExecutor()
    loop = AnalysisLoop(model=GenericFixtureModel(), executor=executor, manifests=[_manifest()])

    clarification = await loop.run(_request(AMBIGUOUS_QUESTION), emit=lambda _event: None)
    unsupported = await loop.run(
        _request("고객 이메일 원본을 전부 export하고 삭제해줘"),
        emit=lambda _event: None,
    )

    assert clarification.status == "awaiting_clarification"
    assert clarification.clarification is not None
    assert clarification.facts == []
    assert unsupported.status == "failed"
    assert unsupported.unsupported is not None
    assert unsupported.error is not None
    assert unsupported.error.code == "unsupported_analysis"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_no_data_is_degraded_without_facts_notes_or_report() -> None:
    loop = AnalysisLoop(
        model=GenericFixtureModel(),
        executor=ScriptedExecutor(no_data=True),
        manifests=[_manifest()],
    )

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=lambda _event: None)

    assert outcome.status == "degraded"
    assert outcome.facts == []
    assert outcome.notes == []
    assert outcome.report is None
    assert outcome.limitations


@pytest.mark.asyncio
async def test_step_failure_preserves_only_completed_facts_and_verified_notes() -> None:
    executor = ScriptedExecutor(fail_after=1)
    loop = AnalysisLoop(model=GenericFixtureModel(), executor=executor, manifests=[_manifest()])

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=lambda _event: None)

    assert outcome.status == "failed"
    assert [fact.step_id for fact in outcome.facts] == ["step-catalog"]
    assert [note.step_id for note in outcome.notes] == ["step-catalog"]
    assert outcome.report is None
    assert outcome.error is not None
    assert outcome.error.step_id == "step-profile"


class ForgedReportFixture(GenericFixtureModel):
    async def create_report(self, context):
        return CustomerSignalReportDraft(
            goal_id=context.goal.goal_id,
            claim_refs=["claim-ffffffffffffffffffffffff"],
            recommended_actions=[],
        )


@pytest.mark.asyncio
async def test_forged_report_claim_reference_fails_without_public_result() -> None:
    loop = AnalysisLoop(
        model=ForgedReportFixture(), executor=ScriptedExecutor(), manifests=[_manifest()]
    )

    outcome = await loop.run(_request(NEGATIVE_TOPIC_QUESTION), emit=lambda _event: None)

    assert outcome.status == "failed"
    assert outcome.facts
    assert outcome.notes
    assert outcome.report is None
    assert outcome.error is not None


def test_generic_outcome_contract_rejects_inconsistent_states() -> None:
    with pytest.raises(ValueError, match="clarification"):
        GenericRunnerOutcome(
            status="awaiting_clarification",
            facts=[],
            notes=[],
            limitations=[],
            agent_mode="fixture",
        )
