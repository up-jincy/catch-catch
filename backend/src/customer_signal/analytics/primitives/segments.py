"""Customer segmentation and verified segment comparison primitives."""

from __future__ import annotations

from collections import defaultdict

from customer_signal.analytics.primitives.common import (
    PrimitiveContext,
    PrimitiveContractError,
    filter_events,
    matches_predicate,
    metric,
    require_single_expected_metric,
    stable_identifier,
)
from customer_signal.domain.facts import (
    AnalysisMetricDelta,
    ProcessingStats,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
)
from customer_signal.domain.primitives import CompareSegmentsInput, SegmentCustomersInput


def segment_customers(
    context: PrimitiveContext,
    parameters: SegmentCustomersInput,
) -> SegmentCustomersPayload:
    selected = filter_events(context, parameters.predicates)
    by_customer: dict[str, int] = defaultdict(int)
    for event in selected:
        context.budget.checkpoint()
        by_customer[event.canonical_customer_id] += 1
    customer_ids = sorted(
        customer_id
        for customer_id, count in by_customer.items()
        if count >= parameters.minimum_matching_events
    )[: context.max_output_rows]
    predicate_counts = {
        predicate: sum(1 for event in context.events if matches_predicate(event, predicate))
        for predicate in parameters.predicates
    }
    return SegmentCustomersPayload(
        kind="segment_customers",
        segment_id=stable_identifier(
            "segment",
            {
                "predicates": parameters.predicates,
                "minimum_matching_events": parameters.minimum_matching_events,
                "scope": context.scope.model_dump(mode="json"),
            },
        ),
        customer_ids=customer_ids,
        predicate_counts=predicate_counts,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(selected),
            returned_rows=len(customer_ids),
        ),
        provenance=context.provenance,
        metrics=[metric("segment_customer_count", len(customer_ids), unit="customers")],
    )


def compare_segments(
    context: PrimitiveContext,
    parameters: CompareSegmentsInput,
) -> SegmentComparisonPayload:
    if len(context.input_facts) != 2:
        raise PrimitiveContractError("compare_segments requires two ordered input Facts")
    baseline_fact, comparison_fact = context.input_facts
    try:
        baseline = baseline_fact.metric(parameters.metric_key)
        comparison = comparison_fact.metric(parameters.metric_key)
    except LookupError as error:
        raise PrimitiveContractError(
            "comparison metric must resolve exactly in both input Facts"
        ) from error
    if baseline.unit != comparison.unit:
        raise PrimitiveContractError("comparison input metric units must match")
    requested_metric_key = require_single_expected_metric(context)
    if requested_metric_key != f"{parameters.metric_key}_delta":
        raise PrimitiveContractError("comparison expected metric must be the requested delta")
    delta = comparison.value - baseline.value
    return SegmentComparisonPayload(
        kind="compare_segments",
        requested_metric_key=requested_metric_key,
        baseline_fact_id=baseline_fact.fact_id,
        comparison_fact_id=comparison_fact.fact_id,
        deltas=[
            AnalysisMetricDelta(
                metric_key=parameters.metric_key,
                baseline=baseline.value,
                comparison=comparison.value,
                delta=delta,
                unit=baseline.unit,
            )
        ],
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=0,
            returned_rows=1,
        ),
        provenance=context.provenance,
        metrics=[metric(requested_metric_key, delta, unit=baseline.unit)],
    )


__all__ = ["compare_segments", "segment_customers"]
