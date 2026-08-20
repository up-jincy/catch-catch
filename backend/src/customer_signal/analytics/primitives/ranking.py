"""Deterministic customer ranking and safe journey projection primitives."""

from __future__ import annotations

import re

from customer_signal.analytics.primitives.common import (
    PrimitiveContext,
    PrimitiveContractError,
    PrimitiveDependencyError,
    metric,
)
from customer_signal.domain.facts import (
    AnalysisJourneyEvent,
    AnalysisRankedCustomer,
    AnalysisSignal,
    CustomerJourneyPayload,
    CustomerRankingPayload,
    ProcessingStats,
)
from customer_signal.domain.primitives import GetCustomerJourneyInput, RankCustomersInput


_METRIC_KEY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def rank_customers(
    context: PrimitiveContext,
    parameters: RankCustomersInput,
) -> CustomerRankingPayload:
    customers = sorted(
        {customer_id for fact in context.input_facts for customer_id in fact.customer_ids}
    )
    ranked: list[AnalysisRankedCustomer] = []
    for customer_id in customers:
        context.budget.checkpoint()
        signals: list[AnalysisSignal] = []
        score = 0.0
        for signal_key, weight in sorted(parameters.weights.items()):
            if _METRIC_KEY.fullmatch(signal_key) is None:
                raise PrimitiveContractError("ranking weights must use semantic metric keys")
            supporting = [
                fact
                for fact in context.input_facts
                if customer_id in fact.customer_ids
                and any(metric_fact.metric_key == signal_key for metric_fact in fact.metrics)
            ]
            if not supporting:
                continue
            contribution = max(0.0, float(weight))
            score += contribution
            signals.append(
                AnalysisSignal(
                    signal_key=signal_key,
                    label=signal_key.replace("_", " ").title(),
                    contribution=contribution,
                    metric_refs=[f"{fact.fact_id}:{signal_key}" for fact in supporting],
                    evidence_ids=[],
                )
            )
        ranked.append(
            AnalysisRankedCustomer(
                customer_id=customer_id,
                score=min(100.0, score),
                signals=signals,
                evidence_ids=[],
            )
        )
    ranked.sort(key=lambda customer: (-customer.score, customer.customer_id))
    ranked = ranked[: min(parameters.limit, context.max_output_rows)]
    return CustomerRankingPayload(
        kind="rank_customers",
        customers=ranked,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(
                {
                    event.event_id
                    for event in context.events
                    if event.canonical_customer_id in {item.customer_id for item in ranked}
                }
            ),
            returned_rows=len(ranked),
        ),
        provenance=context.provenance,
        metrics=[metric("ranked_customer_count", len(ranked), unit="customers")],
    )


def get_customer_journey(
    context: PrimitiveContext,
    parameters: GetCustomerJourneyInput,
) -> CustomerJourneyPayload:
    customer_ids = [
        customer_id for fact in context.input_facts for customer_id in fact.customer_ids
    ]
    customer_ids = list(dict.fromkeys(customer_ids))
    if not customer_ids:
        raise PrimitiveDependencyError("journey dependency authorizes no customer")
    customer_id = customer_ids[0]
    selected = [event for event in context.events if event.canonical_customer_id == customer_id]
    selected.sort(key=lambda event: (event.occurred_at, event.event_id))
    limit = min(parameters.limit, context.max_output_rows, context.max_evidence)
    events = [
        AnalysisJourneyEvent(
            event_id=event.event_id,
            evidence_id=event.evidence_id,
            source_id=event.source_id,
            occurred_at=event.occurred_at,
            event_type=event.event_type,
            action=event.action,
            topic=event.topic,
            outcome=event.outcome,
            text=f"{event.event_type}: topic={event.topic}; outcome={event.outcome}",
        )
        for event in selected[:limit]
    ]
    return CustomerJourneyPayload(
        kind="get_customer_journey",
        customer_id=customer_id,
        events=events,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(selected),
            returned_rows=len(events),
        ),
        provenance=context.provenance,
        metrics=[metric("journey_event_count", len(events), unit="events")],
    )


__all__ = ["get_customer_journey", "rank_customers"]
