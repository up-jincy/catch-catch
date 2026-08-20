"""Acceptance-level RED tests for generic sequence analysis."""

from __future__ import annotations

import time

from customer_signal.analytics.executor import PrimitiveExecutor, RunBudget
from customer_signal.domain.facts import SequenceMatchPayload
from support.primitive_test_support import (
    DATASET_VERSION,
    FIXED_CREATED_AT,
    event_scope,
    repeat_to_voc_step,
    signup_abandonment_step,
    source_registry,
)


def test_repeat_to_voc_and_signup_abandonment_have_distinct_exact_results(
    synthetic_dataset,
) -> None:
    registry = source_registry(
        synthetic_dataset,
        ["search_history", "subscription", "voc"],
    )
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=DATASET_VERSION,
        clock=lambda: FIXED_CREATED_AT,
    )

    repeat_fact = executor.execute(
        repeat_to_voc_step(),
        scope=event_scope(synthetic_dataset, ["search_history", "voc"]),
        prior_facts=[],
        budget=RunBudget(deadline_monotonic=time.monotonic() + 5),
    )
    signup_fact = executor.execute(
        signup_abandonment_step(),
        scope=event_scope(synthetic_dataset, ["subscription"]),
        prior_facts=[],
        budget=RunBudget(deadline_monotonic=time.monotonic() + 5),
    )

    assert isinstance(repeat_fact.payload, SequenceMatchPayload)
    assert isinstance(signup_fact.payload, SequenceMatchPayload)
    assert repeat_fact.metric("matched_customer_count").value == 6
    assert signup_fact.metric("started_customer_count").value == 12
    assert signup_fact.metric("matched_customer_count").value == 7
    assert signup_fact.metric("abandoned_customer_count").value == 5
    assert repeat_fact.customer_ids != signup_fact.customer_ids
