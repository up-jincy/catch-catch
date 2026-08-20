"""Acceptance-level RED tests for generic profile and aggregate primitives."""

from __future__ import annotations

import time

from customer_signal.analytics.executor import PrimitiveExecutor, RunBudget
from customer_signal.domain.facts import AggregateEventsPayload
from support.primitive_test_support import (
    DATASET_VERSION,
    FIXED_CREATED_AT,
    event_scope,
    negative_feedback_step,
    source_registry,
)


def test_negative_feedback_topic_returns_six_customers_with_semantic_metric(
    synthetic_dataset,
) -> None:
    registry = source_registry(synthetic_dataset, ["search_feedback"])
    scope = event_scope(synthetic_dataset, ["search_feedback"])
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )

    fact = executor.execute(
        negative_feedback_step(),
        scope=scope,
        prior_facts=[],
        budget=RunBudget(deadline_monotonic=time.monotonic() + 5),
    )

    assert fact.primitive == "aggregate_events"
    assert isinstance(fact.payload, AggregateEventsPayload)
    metric = fact.metric("negative_feedback_customer_count")
    assert metric.value == 6
    assert metric.unit == "customers"
    assert fact.payload.processing.matched_events == 6
    assert len(fact.payload.buckets) == 1
    bucket = fact.payload.buckets[0]
    assert bucket.dimensions == {"topic": "요금제 변경"}
    assert bucket.event_count == 6
    assert bucket.customer_count == 6
    assert fact.source_ids == ["search_feedback"]
    assert fact.payload.provenance.scope == scope
