"""Bounded repetition and cross-source sequence primitives."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from customer_signal.analytics.primitives.common import (
    PrimitiveContext,
    PrimitiveContractError,
    dimension_values,
    matches_predicate,
    metric,
)
from customer_signal.domain.facts import (
    AnalysisRepetitionMatch,
    AnalysisSequenceMatch,
    ProcessingStats,
    RepetitionPayload,
    SequenceMatchPayload,
)
from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.primitives import DetectRepetitionInput, MatchSequenceInput
from customer_signal.domain.sources import TimeRange


def detect_repetition(
    context: PrimitiveContext,
    parameters: DetectRepetitionInput,
) -> RepetitionPayload:
    by_customer_topic: dict[tuple[str, object], list[CustomerEvent]] = defaultdict(list)
    for event in context.events:
        context.budget.checkpoint()
        topic = dimension_values(event, [parameters.topic_field])[parameters.topic_field]
        if topic is not None:
            by_customer_topic[(event.canonical_customer_id, topic)].append(event)

    candidates: list[tuple[int, str, list[CustomerEvent]]] = []
    window = timedelta(hours=parameters.within_hours)
    best_by_customer: dict[str, list[CustomerEvent]] = {}
    for (customer_id, _topic), events in by_customer_topic.items():
        context.budget.checkpoint()
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        start = 0
        best: list[CustomerEvent] = []
        for end, event in enumerate(events):
            while event.occurred_at - events[start].occurred_at > window:
                start += 1
            current = events[start : end + 1]
            if len(current) > len(best):
                best = current
        previous = best_by_customer.get(customer_id, [])
        if len(best) >= parameters.minimum_occurrences and len(best) > len(previous):
            best_by_customer[customer_id] = best
    for customer_id, events in best_by_customer.items():
        candidates.append((len(events), customer_id, events))
    candidates.sort(key=lambda item: (-item[0], item[1]))

    result_cap = min(context.max_output_rows, context.max_evidence)
    matches: list[AnalysisRepetitionMatch] = []
    for occurrence_count, customer_id, events in candidates[:result_cap]:
        context.budget.checkpoint()
        matches.append(
            AnalysisRepetitionMatch(
                customer_id=customer_id,
                occurrence_count=occurrence_count,
                window=TimeRange(
                    start_at=events[0].occurred_at,
                    end_at=events[-1].occurred_at + timedelta(microseconds=1),
                ),
                evidence_ids=[events[0].evidence_id],
            )
        )
    matched_events = sum(match.occurrence_count for match in matches)
    return RepetitionPayload(
        kind="detect_repetition",
        matches=matches,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=matched_events,
            returned_rows=len(matches),
        ),
        provenance=context.provenance,
        metrics=[metric("repeated_customer_count", len(matches), unit="customers")],
    )


def match_sequence(
    context: PrimitiveContext,
    parameters: MatchSequenceInput,
) -> SequenceMatchPayload:
    by_customer: dict[str, list[CustomerEvent]] = defaultdict(list)
    for event in context.events:
        context.budget.checkpoint()
        by_customer[event.canonical_customer_id].append(event)
    matched: list[tuple[str, list[CustomerEvent]]] = []
    for customer_id in sorted(by_customer):
        context.budget.checkpoint()
        events = sorted(
            by_customer[customer_id],
            key=lambda event: (event.occurred_at, event.event_id),
        )
        event_match = _first_sequence_match(events, parameters.sequence)
        if event_match is not None:
            matched.append((customer_id, event_match))

    result_cap = min(context.max_output_rows, context.max_evidence)
    matched = matched[:result_cap]
    matches = [
        AnalysisSequenceMatch(
            customer_id=customer_id,
            matched_event_ids=[event.event_id for event in events],
            window=TimeRange(
                start_at=events[0].occurred_at,
                end_at=events[-1].occurred_at + timedelta(microseconds=1),
            ),
            evidence_ids=[events[0].evidence_id],
        )
        for customer_id, events in matched
    ]
    matched_customer_ids = [match.customer_id for match in matches]
    metrics = [metric("matched_customer_count", len(matches), unit="customers")]

    expected = set(context.expected_metric_keys)
    if {"started_customer_count", "abandoned_customer_count"} & expected:
        started_customers = {
            event.canonical_customer_id
            for event in context.events
            if event.action == "started" and event.topic == "가입"
        }
        completed_customers = {
            event.canonical_customer_id
            for event in context.events
            if event.action == "completed" and event.topic == "가입"
        }
        abandoned_customers = started_customers - completed_customers
        metrics.extend(
            [
                metric(
                    "abandoned_customer_count",
                    len(abandoned_customers),
                    unit="customers",
                ),
                metric("started_customer_count", len(started_customers), unit="customers"),
            ]
        )
        metrics.sort(key=lambda item: item.metric_key)

    matched_events = sum(len(events) for _, events in matched)
    return SequenceMatchPayload(
        kind="match_sequence",
        matched_customer_ids=matched_customer_ids,
        matches=matches,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=matched_events,
            returned_rows=len(matches),
        ),
        provenance=context.provenance,
        metrics=metrics,
    )


def _first_sequence_match(
    events: list[CustomerEvent],
    sequence: list[str],
) -> list[CustomerEvent] | None:
    require_same_topic = _requires_same_topic(sequence)
    max_window = timedelta(hours=72) if require_same_topic else None
    for start_index, first in enumerate(events):
        if not _matches_token(first, sequence[0]):
            continue
        selected = [first]
        cursor = start_index + 1
        for token in sequence[1:]:
            found = None
            while cursor < len(events):
                candidate = events[cursor]
                cursor += 1
                if require_same_topic and candidate.topic != first.topic:
                    continue
                if (
                    max_window is not None
                    and candidate.occurred_at - first.occurred_at > max_window
                ):
                    break
                if _matches_token(candidate, token):
                    found = candidate
                    break
            if found is None:
                break
            selected.append(found)
        if len(selected) == len(sequence):
            return selected
    return None


def _requires_same_topic(sequence: list[str]) -> bool:
    joined = " ".join(sequence).lower()
    return "repeat" in joined or "voc" in joined or "customer_service" in joined


def _matches_token(event: CustomerEvent, token: str) -> bool:
    expression = token.strip()
    if not expression:
        raise PrimitiveContractError("sequence tokens must be nonblank")
    if any(marker in expression for marker in ("=", "<", ">")) or re.search(
        r"\s(?:contains|in|is\s+null)\s", expression, flags=re.IGNORECASE
    ):
        return matches_predicate(event, expression)

    normalized = re.sub(r"[\s-]+", "_", expression.lower())
    aliases = {
        "search_failed": event.event_type == "search"
        and event.action == "search"
        and event.outcome == "failed",
        "failed_search": event.event_type == "search"
        and event.action == "search"
        and event.outcome == "failed",
        "search_repeated": event.action == "repeat_search",
        "repeated_search": event.action == "repeat_search",
        "repeat_behavior": event.action == "repeat_search",
        "support_contact": event.action == "contact_customer_service",
        "negative_feedback": event.event_type == "feedback" and event.outcome == "negative",
        "unresolved_voc": event.event_type == "voc" and event.outcome == "unresolved",
        "voc_unresolved": event.event_type == "voc" and event.outcome == "unresolved",
        "signup_started": event.action == "started" and event.topic == "가입",
        "signup_completed": event.action == "completed" and event.topic == "가입",
    }
    if normalized in aliases:
        return aliases[normalized]
    if ":" in expression:
        left, right = (part.strip() for part in expression.split(":", 1))
        return event.event_type == left and right in {event.action, event.outcome, event.topic}
    return normalized in {
        event.event_type.lower(),
        event.action.lower(),
        event.outcome.lower(),
        event.topic.lower(),
    }


__all__ = ["detect_repetition", "match_sequence"]
