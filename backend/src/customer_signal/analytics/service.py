"""Pure deterministic analytics over the bounded repository API."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, cast

from pydantic import BaseModel

from customer_signal.analytics.models import (
    AggregateBucket,
    AggregateDimension,
    AggregateResult,
    CatalogSourcesResult,
    CustomerJourneyResult,
    EvidenceResult,
    PatternMatchResult,
    RankCustomersResult,
    ToolStats,
)
from customer_signal.analytics.policies import (
    FAILED_SEARCH_SCORE,
    NEGATIVE_FEEDBACK_SCORE,
    REPEAT_WINDOW_HOURS,
    SAME_TOPIC_FAILED_REPEAT_SCORE,
    SAME_TOPIC_UNRESOLVED_VOC_SCORE,
    VOC_WINDOW_HOURS,
    risk_level_for_score,
)
from customer_signal.data.repository import SOURCE_IDS, SourceCatalogEntry
from customer_signal.domain.models import CustomerEvent, EvidenceRecord, SourceId
from customer_signal.domain.reports import JourneyEvent, RankedCustomer, Signal


_SOURCE_ORDER = {source_id: index for index, source_id in enumerate(SOURCE_IDS)}
_SOURCE_SET = frozenset(SOURCE_IDS)
_REQUIRED_PATTERN_SOURCES = ("search_history", "voc")
_AGGREGATE_DIMENSIONS = frozenset(("source", "topic", "outcome"))


class AnalyticsInputError(ValueError):
    """Raised when a public analytics input violates its bounded contract."""


class AnalyticsRepository(Protocol):
    """Repository operations required by the pure analytics layer."""

    def catalog_sources(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> list[SourceCatalogEntry]: ...

    def list_events(
        self,
        start_at: datetime,
        end_at: datetime,
        enabled_sources: Sequence[str],
        customer_id: str | None = None,
        limit: int = 100,
    ) -> list[CustomerEvent]: ...

    def get_evidence(self, evidence_ids: Sequence[str]) -> list[EvidenceRecord]: ...


def _parse_datetime(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise AnalyticsInputError(f"{field_name} must be a timezone-aware datetime") from error
    else:
        raise AnalyticsInputError(f"{field_name} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnalyticsInputError(f"{field_name} must be a timezone-aware datetime")
    return parsed


def _validate_time_range(
    start_at: datetime | str,
    end_at: datetime | str,
) -> tuple[datetime, datetime]:
    start = _parse_datetime(start_at, "start_at")
    end = _parse_datetime(end_at, "end_at")
    if start >= end:
        raise AnalyticsInputError("start_at must be before end_at")
    return start, end


def _validate_sources(enabled_sources: Sequence[str]) -> list[SourceId]:
    if isinstance(enabled_sources, (str, bytes)) or not isinstance(
        enabled_sources, Sequence
    ):
        raise AnalyticsInputError(
            "enabled_sources must contain 1 to 3 unique allowlisted sources"
        )
    sources = list(enabled_sources)
    if (
        not 1 <= len(sources) <= len(SOURCE_IDS)
        or any(not isinstance(source, str) for source in sources)
        or len(sources) != len(set(sources))
        or any(source not in _SOURCE_SET for source in sources)
    ):
        raise AnalyticsInputError(
            "enabled_sources must contain 1 to 3 unique allowlisted sources"
        )
    return [cast(SourceId, source) for source in SOURCE_IDS if source in sources]


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise AnalyticsInputError("limit must be an integer between 1 and 100")
    return limit


def _validate_customer_id(customer_id: str) -> str:
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise AnalyticsInputError("customer_id must be a nonblank string")
    return customer_id


def _validate_evidence_ids(evidence_ids: Sequence[str]) -> list[str]:
    if isinstance(evidence_ids, (str, bytes)) or not isinstance(evidence_ids, Sequence):
        raise AnalyticsInputError("evidence_ids must contain 1 to 100 nonblank strings")
    identifiers = list(evidence_ids)
    if (
        not 1 <= len(identifiers) <= 100
        or any(not isinstance(identifier, str) or not identifier.strip() for identifier in identifiers)
    ):
        raise AnalyticsInputError("evidence_ids must contain 1 to 100 nonblank strings")
    return identifiers


def _validate_group_by(group_by: str) -> AggregateDimension:
    if not isinstance(group_by, str) or group_by not in _AGGREGATE_DIMENSIONS:
        raise AnalyticsInputError("group_by must be one of: source, topic, outcome")
    return cast(AggregateDimension, group_by)


def _normalized(value: Any) -> Any:
    if isinstance(value, datetime):
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, BaseModel):
        return _normalized(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    return value


def _stable_result_id(operation: str, *, inputs: dict[str, Any], result: dict[str, Any]) -> str:
    document = json.dumps(
        _normalized({"inputs": inputs, "result": result}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()[:16]
    return f"{operation}:{digest}"


def _flatten_evidence(customers: Sequence[RankedCustomer]) -> list[str]:
    return [
        evidence_id
        for customer in customers
        for evidence_id in customer.evidence_ids
    ]


class _ScoredSequence:
    def __init__(
        self,
        customer: RankedCustomer,
        *,
        matched: bool,
        failed_at: datetime,
        topic: str,
    ) -> None:
        self.customer = customer
        self.matched = matched
        self.failed_at = failed_at
        self.topic = topic


def _score_sequence(
    events: Sequence[CustomerEvent],
    failed: CustomerEvent,
) -> _ScoredSequence:
    repeat_deadline = failed.occurred_at + timedelta(hours=REPEAT_WINDOW_HOURS)
    voc_deadline = failed.occurred_at + timedelta(hours=VOC_WINDOW_HOURS)
    repeats = [
        event
        for event in events
        if event.event_type == "search"
        and event.action == "repeat_search"
        and event.outcome == "failed"
        and event.topic == failed.topic
        and failed.occurred_at < event.occurred_at <= repeat_deadline
    ]
    feedback = [
        event
        for event in events
        if event.event_type == "feedback"
        and event.outcome == "negative"
        and event.topic == failed.topic
    ]
    vocs = [
        event
        for event in events
        if event.event_type == "voc"
        and event.outcome == "unresolved"
        and event.topic == failed.topic
        and failed.occurred_at < event.occurred_at <= voc_deadline
    ]

    signals = [
        Signal(
            code="failed_search",
            label="Failed search",
            score=FAILED_SEARCH_SCORE,
            evidence_ids=[failed.evidence_id],
        )
    ]
    if repeats:
        signals.append(
            Signal(
                code="repeated_failed_search",
                label="Same-topic failed repeat within 24 hours",
                score=SAME_TOPIC_FAILED_REPEAT_SCORE,
                evidence_ids=[repeats[0].evidence_id],
            )
        )
    if feedback:
        signals.append(
            Signal(
                code="negative_feedback",
                label="Negative feedback on the same topic",
                score=NEGATIVE_FEEDBACK_SCORE,
                evidence_ids=[feedback[0].evidence_id],
            )
        )
    if vocs:
        signals.append(
            Signal(
                code="unresolved_voc",
                label="Same-topic unresolved VOC within 72 hours",
                score=SAME_TOPIC_UNRESOLVED_VOC_SCORE,
                evidence_ids=[vocs[0].evidence_id],
            )
        )

    score = sum(signal.score for signal in signals)
    evidence_ids = [
        evidence_id for signal in signals for evidence_id in signal.evidence_ids
    ]
    customer = RankedCustomer(
        customer_id=failed.canonical_customer_id,
        risk_score=score,
        risk_level=risk_level_for_score(score),
        signals=signals,
        evidence_ids=evidence_ids,
        last_event_at=max(event.occurred_at for event in events),
    )
    return _ScoredSequence(
        customer,
        matched=bool(repeats and vocs),
        failed_at=failed.occurred_at,
        topic=failed.topic,
    )


def _select_sequences(
    events: Sequence[CustomerEvent],
) -> tuple[list[RankedCustomer], list[RankedCustomer]]:
    grouped: dict[str, list[CustomerEvent]] = defaultdict(list)
    for event in events:
        grouped[event.canonical_customer_id].append(event)

    candidates: list[RankedCustomer] = []
    matches: list[RankedCustomer] = []
    for customer_id in sorted(grouped):
        customer_events = sorted(
            grouped[customer_id], key=lambda event: (event.occurred_at, event.event_id)
        )
        failed_searches = [
            event
            for event in customer_events
            if event.event_type == "search"
            and event.outcome == "failed"
            and event.action != "repeat_search"
        ]
        if not failed_searches:
            continue
        scored = [_score_sequence(customer_events, failed) for failed in failed_searches]
        scored.sort(
            key=lambda sequence: (
                -sequence.customer.risk_score,
                sequence.failed_at,
                sequence.topic,
            )
        )
        candidates.append(scored[0].customer)
        matching = [sequence for sequence in scored if sequence.matched]
        if matching:
            matches.append(matching[0].customer)
    return candidates, matches


class AnalyticsService:
    """Apply fixed analytics policies without exposing storage implementation details."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    def _load_events(
        self,
        start_at: datetime,
        end_at: datetime,
        enabled_sources: Sequence[SourceId],
        *,
        customer_id: str | None = None,
    ) -> list[CustomerEvent]:
        events: list[CustomerEvent] = []
        for source_id in enabled_sources:
            events.extend(
                self._repository.list_events(
                    start_at=start_at,
                    end_at=end_at,
                    enabled_sources=[source_id],
                    customer_id=customer_id,
                    limit=100,
                )
            )
        return sorted(events, key=lambda event: (event.occurred_at, event.event_id))

    def catalog_sources(
        self,
        start_at: datetime | str,
        end_at: datetime | str,
    ) -> CatalogSourcesResult:
        start, end = _validate_time_range(start_at, end_at)
        sources = self._repository.catalog_sources(start, end)
        present = {source.source_id for source in sources}
        missing_sources = [
            cast(SourceId, source_id) for source_id in SOURCE_IDS if source_id not in present
        ]
        stats = ToolStats(
            scanned_rows=sum(source.row_count for source in sources),
            returned_rows=len(sources),
        )
        result_payload = {
            "sources": sources,
            "missing_sources": missing_sources,
            "stats": stats,
        }
        result_id = _stable_result_id(
            "catalog_sources",
            inputs={"start_at": start, "end_at": end},
            result=result_payload,
        )
        return CatalogSourcesResult(result_id=result_id, **result_payload)

    def aggregate_events(
        self,
        start_at: datetime | str,
        end_at: datetime | str,
        enabled_sources: Sequence[str],
        group_by: str = "source",
        limit: int = 100,
    ) -> AggregateResult:
        start, end = _validate_time_range(start_at, end_at)
        sources = _validate_sources(enabled_sources)
        dimension = _validate_group_by(group_by)
        bounded_limit = _validate_limit(limit)
        events = self._load_events(start, end, sources)

        grouped: dict[str, list[CustomerEvent]] = defaultdict(list)
        for event in events:
            if dimension == "source":
                key = event.source_id
            else:
                key = cast(str, getattr(event, dimension))
            grouped[key].append(event)
        if dimension == "source":
            keys = sorted(grouped, key=_SOURCE_ORDER.__getitem__)
        else:
            keys = sorted(grouped)
        buckets = [
            AggregateBucket(
                value=key,
                event_count=len(grouped[key]),
                customer_count=len(
                    {event.canonical_customer_id for event in grouped[key]}
                ),
                evidence_ids=[event.evidence_id for event in grouped[key]],
            )
            for key in keys[:bounded_limit]
        ]
        evidence_ids = [
            evidence_id for bucket in buckets for evidence_id in bucket.evidence_ids
        ]
        stats = ToolStats(scanned_rows=len(events), returned_rows=len(buckets))
        result_payload = {
            "group_by": dimension,
            "buckets": buckets,
            "evidence_ids": evidence_ids,
            "stats": stats,
        }
        result_id = _stable_result_id(
            "aggregate_events",
            inputs={
                "start_at": start,
                "end_at": end,
                "enabled_sources": sources,
                "group_by": dimension,
                "limit": bounded_limit,
            },
            result=result_payload,
        )
        return AggregateResult(result_id=result_id, **result_payload)

    def match_journey_pattern(
        self,
        start_at: datetime | str,
        end_at: datetime | str,
        enabled_sources: Sequence[str],
        limit: int = 100,
    ) -> PatternMatchResult:
        start, end = _validate_time_range(start_at, end_at)
        sources = _validate_sources(enabled_sources)
        bounded_limit = _validate_limit(limit)
        events = self._load_events(start, end, sources)
        candidates, matches = _select_sequences(events)
        matches.sort(key=lambda customer: (-customer.risk_score, customer.customer_id))
        returned = matches[:bounded_limit]
        customer_ids = [customer.customer_id for customer in returned]
        evidence_ids = _flatten_evidence(returned)
        present_sources = {event.source_id for event in events}
        missing_sources = [
            cast(SourceId, source_id)
            for source_id in _REQUIRED_PATTERN_SOURCES
            if source_id not in sources or source_id not in present_sources
        ]
        stats = ToolStats(scanned_rows=len(events), returned_rows=len(returned))
        result_payload = {
            "candidate_count": len(candidates),
            "customer_count": len(returned),
            "customer_ids": customer_ids,
            "customers": returned,
            "missing_sources": missing_sources,
            "evidence_ids": evidence_ids,
            "stats": stats,
        }
        result_id = _stable_result_id(
            "match_journey_pattern",
            inputs={
                "start_at": start,
                "end_at": end,
                "enabled_sources": sources,
                "limit": bounded_limit,
            },
            result=result_payload,
        )
        return PatternMatchResult(result_id=result_id, **result_payload)

    def rank_customers(
        self,
        start_at: datetime | str,
        end_at: datetime | str,
        enabled_sources: Sequence[str],
        limit: int = 100,
    ) -> RankCustomersResult:
        start, end = _validate_time_range(start_at, end_at)
        sources = _validate_sources(enabled_sources)
        bounded_limit = _validate_limit(limit)
        events = self._load_events(start, end, sources)
        candidates, _ = _select_sequences(events)
        candidates.sort(key=lambda customer: (-customer.risk_score, customer.customer_id))
        returned = candidates[:bounded_limit]
        evidence_ids = _flatten_evidence(returned)
        stats = ToolStats(scanned_rows=len(events), returned_rows=len(returned))
        result_payload = {
            "candidate_count": len(candidates),
            "customer_count": len(returned),
            "customers": returned,
            "evidence_ids": evidence_ids,
            "stats": stats,
        }
        result_id = _stable_result_id(
            "rank_customers",
            inputs={
                "start_at": start,
                "end_at": end,
                "enabled_sources": sources,
                "limit": bounded_limit,
            },
            result=result_payload,
        )
        return RankCustomersResult(result_id=result_id, **result_payload)

    def get_customer_journey(
        self,
        customer_id: str,
        start_at: datetime | str,
        end_at: datetime | str,
        enabled_sources: Sequence[str],
        limit: int = 100,
    ) -> CustomerJourneyResult:
        identifier = _validate_customer_id(customer_id)
        start, end = _validate_time_range(start_at, end_at)
        sources = _validate_sources(enabled_sources)
        bounded_limit = _validate_limit(limit)
        events = self._load_events(start, end, sources, customer_id=identifier)
        returned_events = events[:bounded_limit]
        journey_events = [
            JourneyEvent(
                event_id=event.event_id,
                evidence_id=event.evidence_id,
                source_id=event.source_id,
                occurred_at=event.occurred_at,
                event_type=event.event_type,
                action=event.action,
                topic=event.topic,
                outcome=event.outcome,
                text=event.text,
            )
            for event in returned_events
        ]
        evidence_ids = [event.evidence_id for event in journey_events]
        stats = ToolStats(scanned_rows=len(events), returned_rows=len(journey_events))
        result_payload = {
            "customer_id": identifier,
            "events": journey_events,
            "evidence_ids": evidence_ids,
            "stats": stats,
        }
        result_id = _stable_result_id(
            "get_customer_journey",
            inputs={
                "customer_id": identifier,
                "start_at": start,
                "end_at": end,
                "enabled_sources": sources,
                "limit": bounded_limit,
            },
            result=result_payload,
        )
        return CustomerJourneyResult(result_id=result_id, **result_payload)

    def get_evidence(self, evidence_ids: Sequence[str]) -> EvidenceResult:
        identifiers = _validate_evidence_ids(evidence_ids)
        records = self._repository.get_evidence(identifiers)
        stats = ToolStats(scanned_rows=len(records), returned_rows=len(records))
        result_payload = {
            "records": records,
            "evidence_ids": identifiers,
            "stats": stats,
        }
        result_id = _stable_result_id(
            "get_evidence",
            inputs={"evidence_ids": identifiers},
            result=result_payload,
        )
        return EvidenceResult(result_id=result_id, **result_payload)


__all__ = ["AnalyticsInputError", "AnalyticsService"]
