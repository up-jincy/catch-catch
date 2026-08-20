"""Security, determinism, budget and cancellation RED tests for the Fact executor."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from threading import Event

import pytest

from customer_signal.analytics.executor import (
    PrimitiveCancelledError,
    PrimitiveContractError,
    PrimitiveDependencyError,
    PrimitiveExecutor,
    PrimitiveLimitError,
    PrimitiveTimeoutError,
    RunBudget,
)
from customer_signal.analytics.primitives import HANDLERS, HandlerSpec
from customer_signal.data.source_registry import SourceRegistry
from customer_signal.domain.facts import (
    AnalysisDistributionBucket,
    AnalysisMetricFact,
    ProcessingStats,
    ProfileEventsPayload,
    SegmentCustomersPayload,
    build_fact,
)
from customer_signal.domain.primitives import (
    CompareSegmentsInput,
    GetCustomerJourneyInput,
    GetEvidenceInput,
    ProfileEventsInput,
    RankCustomersInput,
)
from customer_signal.synthetic.manifest import synthetic_source_manifest
from support.in_memory_adapter import InMemorySourceAdapter
from support.primitive_test_support import (
    DATASET_VERSION,
    FIXED_CREATED_AT,
    analysis_step,
    event_scope,
    negative_feedback_step,
    provenance,
    repeat_to_voc_step,
    source_registry,
    step_limits,
)


def _budget(seconds: float = 5) -> RunBudget:
    return RunBudget(deadline_monotonic=time.monotonic() + seconds)


def _executor(synthetic_dataset, *, handlers=HANDLERS, version=DATASET_VERSION):
    registry = source_registry(synthetic_dataset)
    return (
        PrimitiveExecutor(
            registry=registry,
            dataset_version=version,
            handlers=handlers,
            clock=lambda: FIXED_CREATED_AT,
        ),
        registry,
    )


def _profile_step(*, source_ids=("search_feedback",), limits=None):
    return analysis_step(
        step_id="step-profile",
        primitive="profile_events",
        parameters=ProfileEventsInput(primitive="profile_events", group_by=["outcome"]),
        source_ids=source_ids,
        metric_keys=["customer_count", "event_count"],
        limits=limits,
    )


def _segment_fact(registry, scope, *, fact_id: str, step_id: str):
    metric = AnalysisMetricFact(
        metric_key="segment_customer_count",
        label="Segment customers",
        value=1,
        unit="customers",
    )
    payload = SegmentCustomersPayload(
        kind="segment_customers",
        processing=ProcessingStats(scanned_events=1, matched_events=1, returned_rows=1),
        provenance=provenance(registry, scope),
        metrics=[metric],
        segment_id=f"segment-{fact_id}",
        customer_ids=["CUST-001"],
        predicate_counts={"outcome=negative": 1},
    )
    return build_fact(
        fact_id=fact_id,
        step_id=step_id,
        primitive="segment_customers",
        result_id=f"result-{fact_id}",
        payload=payload,
        scope=scope,
        created_at=FIXED_CREATED_AT,
    )


def test_fact_ids_are_deterministic_and_bound_to_scope_limits_and_version(
    synthetic_dataset,
) -> None:
    executor, _ = _executor(synthetic_dataset)
    scope = event_scope(synthetic_dataset, ["search_feedback"])
    step = negative_feedback_step()

    first = executor.execute(step, scope=scope, prior_facts=[], budget=_budget())
    replay = executor.execute(step, scope=scope, prior_facts=[], budget=_budget())
    time_changed = executor.execute(
        step,
        scope=scope.model_copy(update={"start_at": scope.start_at + timedelta(microseconds=1)}),
        prior_facts=[],
        budget=_budget(),
    )
    limited_step = negative_feedback_step(
        limits=step_limits(max_output_rows=10),
    )
    limits_changed = executor.execute(
        limited_step,
        scope=scope,
        prior_facts=[],
        budget=_budget(),
    )
    source_step = step.model_copy(update={"source_ids": ["search_history"]})
    source_changed = executor.execute(
        source_step,
        scope=event_scope(synthetic_dataset, ["search_history"]),
        prior_facts=[],
        budget=_budget(),
    )
    versioned_executor, _ = _executor(
        synthetic_dataset,
        version=f"{DATASET_VERSION}-next",
    )
    version_changed = versioned_executor.execute(
        step,
        scope=scope,
        prior_facts=[],
        budget=_budget(),
    )

    assert replay.fact_id == first.fact_id
    assert replay.result_id == first.result_id
    assert replay.payload == first.payload
    assert (
        len(
            {
                first.fact_id,
                time_changed.fact_id,
                limits_changed.fact_id,
                source_changed.fact_id,
                version_changed.fact_id,
            }
        )
        == 5
    )
    assert (
        len(
            {
                first.result_id,
                time_changed.result_id,
                limits_changed.result_id,
                source_changed.result_id,
                version_changed.result_id,
            }
        )
        == 5
    )


def test_executor_rejects_missing_dependency_and_source_expansion(synthetic_dataset) -> None:
    executor, registry = _executor(synthetic_dataset)
    voc_scope = event_scope(synthetic_dataset, ["voc"])
    baseline = _segment_fact(
        registry,
        voc_scope,
        fact_id="fact-baseline",
        step_id="step-baseline",
    )
    compare = analysis_step(
        step_id="step-compare",
        primitive="compare_segments",
        parameters=CompareSegmentsInput(
            primitive="compare_segments",
            metric_key="segment_customer_count",
        ),
        source_ids=["voc"],
        input_step_ids=["step-baseline", "step-comparison"],
        metric_keys=["segment_customer_count_delta"],
    )

    with pytest.raises(PrimitiveContractError, match="dependency"):
        executor.execute(compare, scope=voc_scope, prior_facts=[baseline], budget=_budget())

    with pytest.raises(PrimitiveContractError, match="source"):
        executor.execute(
            _profile_step(source_ids=("voc",)),
            scope=event_scope(synthetic_dataset, ["search_feedback"]),
            prior_facts=[],
            budget=_budget(),
        )


def test_executor_rejects_input_truncation_instead_of_analyzing_a_subset(
    synthetic_dataset,
) -> None:
    executor, _ = _executor(synthetic_dataset)
    scope = event_scope(synthetic_dataset, ["search_feedback"])
    step = negative_feedback_step(limits=step_limits(max_input_events=5))

    with pytest.raises(PrimitiveLimitError, match="input"):
        executor.execute(step, scope=scope, prior_facts=[], budget=_budget())


def test_executor_rejects_dependency_from_another_source_or_dataset(
    synthetic_dataset,
) -> None:
    executor, registry = _executor(synthetic_dataset)
    feedback_scope = event_scope(synthetic_dataset, ["search_feedback"])
    voc_scope = event_scope(synthetic_dataset, ["voc"])
    foreign_source = _segment_fact(
        registry,
        voc_scope,
        fact_id="fact-foreign-source",
        step_id="step-segment",
    )
    current = _segment_fact(
        registry,
        feedback_scope,
        fact_id="fact-foreign-version",
        step_id="step-segment",
    )
    foreign_version = current.model_copy(
        update={
            "payload": current.payload.model_copy(
                update={
                    "provenance": provenance(
                        registry,
                        feedback_scope,
                        dataset_version="foreign-dataset",
                    )
                }
            )
        }
    )
    rank_step = analysis_step(
        step_id="step-rank",
        primitive="rank_customers",
        parameters=RankCustomersInput(
            primitive="rank_customers",
            weights={"segment_customer_count": 1.0},
        ),
        source_ids=["search_feedback"],
        input_step_ids=["step-segment"],
        metric_keys=["ranked_customer_count"],
    )

    for foreign_fact in (foreign_source, foreign_version):
        with pytest.raises(PrimitiveDependencyError, match="dependency"):
            executor.execute(
                rank_step,
                scope=feedback_scope,
                prior_facts=[foreign_fact],
                budget=_budget(),
            )


def test_executor_rejects_ambiguous_ten_thousand_event_truncation(
    synthetic_dataset,
) -> None:
    source_ids = ["search_feedback", "subscription"]
    adapters = []
    for source_index, source_id in enumerate(source_ids):
        base = next(event for event in synthetic_dataset.events if event.source_id == source_id)
        events = [
            base.model_copy(
                update={
                    "event_id": f"OVF-{source_index}-{index:05d}",
                    "evidence_id": f"OVF-E-{source_index}-{index:05d}",
                }
            )
            for index in range(6_000)
        ]
        adapters.append(
            InMemorySourceAdapter(
                synthetic_source_manifest(source_id, synthetic_dataset.events),
                events,
                synthetic_dataset.identity_edges,
                [],
            )
        )

    class UnusedEvidenceProvider:
        def get_evidence(self, _allowed_evidence_ids):
            raise AssertionError("profile overflow must fail before evidence retrieval")

    registry = SourceRegistry(adapters, evidence=UnusedEvidenceProvider())
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )
    scope = event_scope(synthetic_dataset, source_ids, max_events=10_000)

    with pytest.raises(PrimitiveLimitError, match="input"):
        executor.execute(
            _profile_step(
                source_ids=tuple(source_ids),
                limits=step_limits(max_input_events=10_000),
            ),
            scope=scope,
            prior_facts=[],
            budget=_budget(),
        )


def test_empty_journey_dependency_is_not_reported_as_empty_source_scope(
    synthetic_dataset,
) -> None:
    executor, registry = _executor(synthetic_dataset)
    scope = event_scope(synthetic_dataset, ["search_feedback"])
    empty_metric = AnalysisMetricFact(
        metric_key="segment_customer_count",
        label="Segment customers",
        value=0,
        unit="customers",
    )
    payload = SegmentCustomersPayload(
        kind="segment_customers",
        processing=ProcessingStats(scanned_events=1, matched_events=0, returned_rows=0),
        provenance=provenance(registry, scope),
        metrics=[empty_metric],
        segment_id="segment-empty",
        customer_ids=[],
        predicate_counts={},
    )
    empty_fact = build_fact(
        fact_id="fact-empty",
        step_id="step-empty-segment",
        primitive="segment_customers",
        result_id="result-empty",
        payload=payload,
        scope=scope,
        created_at=FIXED_CREATED_AT,
    )
    journey_step = analysis_step(
        step_id="step-empty-journey",
        primitive="get_customer_journey",
        parameters=GetCustomerJourneyInput(
            primitive="get_customer_journey",
            limit=20,
        ),
        source_ids=["search_feedback"],
        input_step_ids=[empty_fact.step_id],
        metric_keys=["journey_event_count"],
    )

    with pytest.raises(PrimitiveDependencyError, match="customer"):
        executor.execute(
            journey_step,
            scope=scope,
            prior_facts=[empty_fact],
            budget=_budget(),
        )


def test_evidence_ids_are_stable_across_fresh_registry_masking_keys(
    synthetic_dataset,
) -> None:
    source_ids = ["search_history", "voc"]
    first_registry = source_registry(synthetic_dataset, source_ids)
    second_registry = source_registry(synthetic_dataset, source_ids)
    first_executor = PrimitiveExecutor(
        registry=first_registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )
    second_executor = PrimitiveExecutor(
        registry=second_registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )
    scope = event_scope(synthetic_dataset, source_ids)
    sequence_step = repeat_to_voc_step()
    sequence_fact = first_executor.execute(
        sequence_step,
        scope=scope,
        prior_facts=[],
        budget=_budget(),
    )
    evidence_step = analysis_step(
        step_id="step-repeat-evidence",
        primitive="get_evidence",
        parameters=GetEvidenceInput(primitive="get_evidence", limit=5),
        source_ids=source_ids,
        input_step_ids=[sequence_step.step_id],
        metric_keys=["evidence_record_count"],
    )

    first = first_executor.execute(
        evidence_step,
        scope=scope,
        prior_facts=[sequence_fact],
        budget=_budget(),
    )
    second = second_executor.execute(
        evidence_step,
        scope=scope,
        prior_facts=[sequence_fact],
        budget=_budget(),
    )

    assert first.payload != second.payload
    assert first.result_id == second.result_id
    assert first.fact_id == second.fact_id


@pytest.mark.parametrize("limit_kind", ["rows", "evidence"])
def test_executor_rejects_handler_payload_beyond_step_output_limits(
    synthetic_dataset,
    limit_kind: str,
) -> None:
    registry = source_registry(synthetic_dataset, ["search_feedback"])
    scope = event_scope(synthetic_dataset, ["search_feedback"])
    metrics = [
        AnalysisMetricFact(
            metric_key="customer_count",
            label="Customers",
            value=2,
            unit="customers",
        ),
        AnalysisMetricFact(
            metric_key="event_count",
            label="Events",
            value=2,
            unit="events",
        ),
    ]
    if limit_kind == "rows":
        distributions = [
            AnalysisDistributionBucket(
                dimensions={"outcome": "negative"},
                event_count=1,
                customer_count=1,
                evidence_ids=["EVD-A"],
            ),
            AnalysisDistributionBucket(
                dimensions={"outcome": "positive"},
                event_count=1,
                customer_count=1,
                evidence_ids=["EVD-B"],
            ),
        ]
        limits = step_limits(max_output_rows=1)
    else:
        distributions = [
            AnalysisDistributionBucket(
                dimensions={"outcome": "negative"},
                event_count=2,
                customer_count=2,
                evidence_ids=["EVD-A", "EVD-B"],
            )
        ]
        limits = step_limits(max_output_rows=2, max_evidence=1)
    payload = ProfileEventsPayload(
        kind="profile_events",
        processing=ProcessingStats(
            scanned_events=2,
            matched_events=2,
            returned_rows=len(distributions),
        ),
        provenance=provenance(registry, scope),
        metrics=metrics,
        distributions=distributions,
        data_quality=[],
    )

    def oversized_handler(_context, _parameters):
        return payload

    handlers = dict(HANDLERS)
    handlers["profile_events"] = HandlerSpec(
        input_type=ProfileEventsInput,
        output_type=ProfileEventsPayload,
        handler=oversized_handler,
    )
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=DATASET_VERSION,
        handlers=handlers,
        clock=lambda: FIXED_CREATED_AT,
    )

    with pytest.raises(PrimitiveLimitError, match="rows|evidence"):
        executor.execute(
            _profile_step(limits=limits),
            scope=scope,
            prior_facts=[],
            budget=_budget(),
        )


def test_expired_budget_fails_before_handler_execution(synthetic_dataset) -> None:
    called = False

    def forbidden_handler(_context, _parameters):
        nonlocal called
        called = True
        raise AssertionError("expired work must not start")

    handlers = dict(HANDLERS)
    handlers["profile_events"] = HandlerSpec(
        input_type=ProfileEventsInput,
        output_type=ProfileEventsPayload,
        handler=forbidden_handler,
    )
    executor, _ = _executor(synthetic_dataset, handlers=handlers)

    with pytest.raises(PrimitiveTimeoutError):
        executor.execute(
            _profile_step(),
            scope=event_scope(synthetic_dataset, ["search_feedback"]),
            prior_facts=[],
            budget=RunBudget(deadline_monotonic=time.monotonic() - 1),
        )
    assert called is False


@pytest.mark.asyncio
async def test_async_cancellation_stops_worker_before_returning_control(
    synthetic_dataset,
) -> None:
    started = Event()
    stopped = Event()

    def blocking_handler(context, _parameters):
        started.set()
        hard_stop = time.monotonic() + 2
        try:
            while time.monotonic() < hard_stop:
                context.budget.checkpoint()
                time.sleep(0.001)
            raise AssertionError("cancelled worker did not observe its shared budget")
        except PrimitiveCancelledError:
            raise
        finally:
            stopped.set()

    handlers = dict(HANDLERS)
    handlers["profile_events"] = HandlerSpec(
        input_type=ProfileEventsInput,
        output_type=ProfileEventsPayload,
        handler=blocking_handler,
    )
    executor, _ = _executor(synthetic_dataset, handlers=handlers)
    budget = _budget(10)
    task = asyncio.create_task(
        executor.execute_async(
            _profile_step(),
            scope=event_scope(synthetic_dataset, ["search_feedback"]),
            prior_facts=[],
            budget=budget,
        )
    )
    assert await asyncio.to_thread(started.wait, 1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert stopped.is_set()
