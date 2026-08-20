"""Acceptance tests for generic customer segments and comparisons."""

from __future__ import annotations

import time

from customer_signal.analytics.executor import PrimitiveExecutor, RunBudget
from customer_signal.domain.facts import SegmentComparisonPayload, SegmentCustomersPayload
from customer_signal.domain.primitives import CompareSegmentsInput, SegmentCustomersInput
from support.primitive_test_support import (
    DATASET_VERSION,
    FIXED_CREATED_AT,
    analysis_step,
    event_scope,
    provenance,
    source_registry,
)


def _segment_step(
    step_id: str,
    predicates: list[str],
    *,
    source_ids: list[str],
    minimum_matching_events: int = 1,
):
    return analysis_step(
        step_id=step_id,
        primitive="segment_customers",
        parameters=SegmentCustomersInput(
            primitive="segment_customers",
            predicates=predicates,
            minimum_matching_events=minimum_matching_events,
        ),
        source_ids=source_ids,
        metric_keys=["segment_customer_count"],
    )


def _execute(executor, step, scope, prior_facts=()):
    return executor.execute(
        step,
        scope=scope,
        prior_facts=prior_facts,
        budget=RunBudget(deadline_monotonic=time.monotonic() + 5),
    )


def test_segment_customers_ands_predicates_before_minimum_event_membership(
    synthetic_dataset,
) -> None:
    source_ids = ["search_history", "subscription"]
    registry = source_registry(synthetic_dataset, source_ids)
    scope = event_scope(synthetic_dataset, source_ids)
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )
    predicates = [
        "outcome in ['pending', 'success']",
        "stage in ['application', 'activated']",
    ]

    fact = _execute(
        executor,
        _segment_step(
            "step-signup-repeat-members",
            predicates,
            source_ids=source_ids,
            minimum_matching_events=2,
        ),
        scope,
    )

    assert isinstance(fact.payload, SegmentCustomersPayload)
    assert fact.customer_ids == [f"CUST-{index:03d}" for index in range(1, 8)]
    assert fact.metric("segment_customer_count").value == 7
    assert fact.payload.predicate_counts == {predicates[0]: 31, predicates[1]: 19}
    assert fact.payload.processing.matched_events == 19
    assert fact.payload.input_fact_ids == []
    assert fact.payload.provenance == provenance(registry, scope)


def test_compare_segments_uses_declared_fact_order_for_delta_and_provenance(
    synthetic_dataset,
) -> None:
    source_ids = ["subscription"]
    registry = source_registry(synthetic_dataset, source_ids)
    scope = event_scope(synthetic_dataset, source_ids)
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )
    baseline_step = _segment_step(
        "step-activated",
        ["stage == 'activated'"],
        source_ids=source_ids,
    )
    comparison_step = _segment_step(
        "step-applied",
        ["stage == 'application'"],
        source_ids=source_ids,
    )
    baseline = _execute(executor, baseline_step, scope)
    comparison = _execute(executor, comparison_step, scope)
    compare_step = analysis_step(
        step_id="step-compare-signup-segments",
        primitive="compare_segments",
        parameters=CompareSegmentsInput(
            primitive="compare_segments",
            metric_key="segment_customer_count",
        ),
        source_ids=source_ids,
        input_step_ids=[baseline_step.step_id, comparison_step.step_id],
        metric_keys=["segment_customer_count_delta"],
    )

    fact = _execute(executor, compare_step, scope, [comparison, baseline])

    assert isinstance(fact.payload, SegmentComparisonPayload)
    assert fact.payload.input_fact_ids == [baseline.fact_id, comparison.fact_id]
    assert fact.payload.baseline_fact_id == baseline.fact_id
    assert fact.payload.comparison_fact_id == comparison.fact_id
    delta = fact.payload.deltas[0]
    assert (delta.baseline, delta.comparison, delta.delta, delta.unit) == (7, 12, 5, "customers")
    assert fact.metric("segment_customer_count_delta").value == 5
    assert fact.payload.provenance == provenance(registry, scope)
