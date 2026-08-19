from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from customer_signal.analytics.models import ToolStats
from customer_signal.analytics.policies import (
    FAILED_SEARCH_SCORE,
    HIGH_RISK_MIN_SCORE,
    MEDIUM_RISK_MIN_SCORE,
    NEGATIVE_FEEDBACK_SCORE,
    SAME_TOPIC_FAILED_REPEAT_SCORE,
    SAME_TOPIC_UNRESOLVED_VOC_SCORE,
    risk_level_for_score,
)
from customer_signal.analytics.service import (
    AnalyticsDataLimitError,
    AnalyticsInputError,
    AnalyticsService,
)
from customer_signal.data.repository import (
    DuckDBRepository,
    EntityNotFoundError,
    SourceCatalogEntry,
)
from customer_signal.domain.models import CustomerEvent, EvidenceRecord


SEOUL = ZoneInfo("Asia/Seoul")
START_AT = datetime(2026, 7, 20, tzinfo=SEOUL)
END_AT = datetime(2026, 8, 19, tzinfo=SEOUL)
ALL_SOURCES = ["search_history", "search_feedback", "voc"]
EXPECTED_MATCHES = [
    "CUST-003",
    "CUST-007",
    "CUST-011",
    "CUST-016",
    "CUST-022",
    "CUST-028",
]


@pytest.fixture
def analytics_service(repository: DuckDBRepository) -> AnalyticsService:
    return AnalyticsService(repository)


def _event(
    event_id: str,
    customer_id: str,
    occurred_at: datetime,
    *,
    source_id: str,
    event_type: str,
    action: str,
    outcome: str,
    topic: str = "인터넷 장애",
) -> CustomerEvent:
    return CustomerEvent(
        event_id=event_id,
        evidence_id=event_id.replace("EVT", "EVD"),
        source_id=source_id,
        occurred_at=occurred_at,
        event_type=event_type,
        action=action,
        topic=topic,
        outcome=outcome,
        text=f"{topic} {outcome}",
        canonical_customer_id=customer_id,
    )


class FakeRepository:
    def __init__(
        self,
        events: Sequence[CustomerEvent],
        evidence: Sequence[EvidenceRecord] = (),
    ) -> None:
        self.events = list(events)
        self.evidence = {record.evidence_id: record for record in evidence}
        self.list_calls: list[dict[str, object]] = []

    def catalog_sources(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> list[SourceCatalogEntry]:
        entries: list[SourceCatalogEntry] = []
        for source_id in ALL_SOURCES:
            rows = [
                event
                for event in self.events
                if event.source_id == source_id and start_at <= event.occurred_at < end_at
            ]
            if rows:
                entries.append(
                    SourceCatalogEntry(
                        source_id=source_id,
                        start_at=min(event.occurred_at for event in rows),
                        end_at=max(event.occurred_at for event in rows),
                        row_count=len(rows),
                    )
                )
        return entries

    def list_events(
        self,
        start_at: datetime,
        end_at: datetime,
        enabled_sources: Sequence[str],
        customer_id: str | None = None,
        limit: int = 100,
    ) -> list[CustomerEvent]:
        self.list_calls.append(
            {
                "start_at": start_at,
                "end_at": end_at,
                "enabled_sources": list(enabled_sources),
                "customer_id": customer_id,
                "limit": limit,
            }
        )
        if customer_id is not None and not any(
            event.canonical_customer_id == customer_id for event in self.events
        ):
            raise EntityNotFoundError(f"customer not found: {customer_id}")
        return sorted(
            (
                event
                for event in self.events
                if start_at <= event.occurred_at < end_at
                and event.source_id in enabled_sources
                and (customer_id is None or event.canonical_customer_id == customer_id)
            ),
            key=lambda event: (event.occurred_at, event.event_id),
        )[:limit]

    def get_evidence(self, evidence_ids: Sequence[str]) -> list[EvidenceRecord]:
        missing = [identifier for identifier in evidence_ids if identifier not in self.evidence]
        if missing:
            raise EntityNotFoundError(f"evidence not found: {', '.join(missing)}")
        return [self.evidence[identifier] for identifier in evidence_ids]


def _full_pattern(
    customer_id: str,
    *,
    repeat_delta: timedelta = timedelta(hours=1),
    voc_delta: timedelta = timedelta(hours=2),
    include_feedback: bool = True,
) -> list[CustomerEvent]:
    failed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    events = [
        _event(
            f"EVT-{customer_id}-01",
            customer_id,
            failed_at,
            source_id="search_history",
            event_type="search",
            action="search",
            outcome="failed",
        ),
        _event(
            f"EVT-{customer_id}-02",
            customer_id,
            failed_at + repeat_delta,
            source_id="search_history",
            event_type="search",
            action="repeat_search",
            outcome="failed",
        ),
        _event(
            f"EVT-{customer_id}-04",
            customer_id,
            failed_at + voc_delta,
            source_id="voc",
            event_type="voc",
            action="contact_customer_service",
            outcome="unresolved",
        ),
    ]
    if include_feedback:
        feedback_at = failed_at + min(repeat_delta + timedelta(minutes=1), voc_delta)
        events.append(
            _event(
                f"EVT-{customer_id}-03",
                customer_id,
                feedback_at,
                source_id="search_feedback",
                event_type="feedback",
                action="submit_feedback",
                outcome="negative",
            )
        )
    return events


def test_seeded_pattern_match_returns_exact_ordered_customers_and_stats(
    analytics_service: AnalyticsService,
):
    result = analytics_service.match_journey_pattern(
        start_at=START_AT.isoformat(),
        end_at=END_AT.isoformat(),
        enabled_sources=ALL_SOURCES,
    )

    assert result.customer_ids == EXPECTED_MATCHES
    assert result.customer_count == 6
    assert result.candidate_count >= result.customer_count
    assert [customer.customer_id for customer in result.customers] == EXPECTED_MATCHES
    assert all(customer.risk_score >= 75 for customer in result.customers)
    assert result.missing_sources == []
    assert result.stats == ToolStats(scanned_rows=108, returned_rows=6)
    assert result.evidence_ids == [
        evidence_id for customer in result.customers for evidence_id in customer.evidence_ids
    ]
    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


def test_disabling_voc_keeps_candidates_but_removes_complete_matches(
    analytics_service: AnalyticsService,
):
    result = analytics_service.match_journey_pattern(
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=["search_history", "search_feedback"],
    )

    assert result.customer_count == 0
    assert result.customer_ids == []
    assert result.customers == []
    assert result.candidate_count >= 6
    assert result.missing_sources == ["voc"]
    assert result.stats.scanned_rows == 84


def test_pattern_boundaries_include_exactly_24_and_72_hours_and_exclude_just_over():
    epsilon = timedelta(microseconds=1)
    events = [
        *_full_pattern(
            "CUST-EXACT",
            repeat_delta=timedelta(hours=24),
            voc_delta=timedelta(hours=72),
            include_feedback=False,
        ),
        *_full_pattern(
            "CUST-LATE-REPEAT",
            repeat_delta=timedelta(hours=24) + epsilon,
            voc_delta=timedelta(hours=48),
            include_feedback=False,
        ),
        *_full_pattern(
            "CUST-LATE-VOC",
            repeat_delta=timedelta(hours=12),
            voc_delta=timedelta(hours=72) + epsilon,
            include_feedback=False,
        ),
    ]
    service = AnalyticsService(FakeRepository(events))

    result = service.match_journey_pattern(
        start_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        enabled_sources=["search_history", "voc"],
    )

    assert result.customer_ids == ["CUST-EXACT"]
    assert result.customers[0].risk_score == 80
    assert [signal.code for signal in result.customers[0].signals] == [
        "failed_search",
        "repeated_failed_search",
        "unresolved_voc",
    ]


def test_voc_before_selected_repeat_is_not_a_match_or_scored_voc_signal():
    events = _full_pattern(
        "CUST-001",
        repeat_delta=timedelta(hours=2),
        voc_delta=timedelta(hours=1),
        include_feedback=False,
    )
    service = AnalyticsService(FakeRepository(events))
    start_at = datetime(2026, 7, 31, tzinfo=timezone.utc)
    end_at = datetime(2026, 8, 2, tzinfo=timezone.utc)

    matched = service.match_journey_pattern(start_at, end_at, ALL_SOURCES)
    ranked = service.rank_customers(start_at, end_at, ALL_SOURCES)

    assert matched.customer_ids == []
    assert ranked.customers[0].risk_score == 50
    assert [signal.code for signal in ranked.customers[0].signals] == [
        "failed_search",
        "repeated_failed_search",
    ]


def test_multiple_failures_topics_and_repeats_select_a_valid_chronological_triple():
    failed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    customer_id = "CUST-001"
    events = [
        _event(
            "EVT-INVALID-FAILED",
            customer_id,
            failed_at,
            source_id="search_history",
            event_type="search",
            action="search",
            outcome="failed",
            topic="요금",
        ),
        _event(
            "EVT-INVALID-VOC",
            customer_id,
            failed_at + timedelta(hours=1),
            source_id="voc",
            event_type="voc",
            action="contact_customer_service",
            outcome="unresolved",
            topic="요금",
        ),
        _event(
            "EVT-INVALID-REPEAT",
            customer_id,
            failed_at + timedelta(hours=2),
            source_id="search_history",
            event_type="search",
            action="repeat_search",
            outcome="failed",
            topic="요금",
        ),
        _event(
            "EVT-VALID-FAILED",
            customer_id,
            failed_at + timedelta(hours=3),
            source_id="search_history",
            event_type="search",
            action="search",
            outcome="failed",
            topic="인터넷 장애",
        ),
        _event(
            "EVT-VALID-REPEAT",
            customer_id,
            failed_at + timedelta(hours=4),
            source_id="search_history",
            event_type="search",
            action="repeat_search",
            outcome="failed",
            topic="인터넷 장애",
        ),
        _event(
            "EVT-VALID-VOC",
            customer_id,
            failed_at + timedelta(hours=5),
            source_id="voc",
            event_type="voc",
            action="contact_customer_service",
            outcome="unresolved",
            topic="인터넷 장애",
        ),
    ]
    service = AnalyticsService(FakeRepository(events))

    result = service.match_journey_pattern(
        start_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        enabled_sources=["search_history", "voc"],
    )

    assert result.customer_ids == [customer_id]
    assert result.customers[0].evidence_ids == [
        "EVD-VALID-FAILED",
        "EVD-VALID-REPEAT",
        "EVD-VALID-VOC",
    ]


def test_negative_feedback_same_topic_adds_twenty_points_but_is_not_required():
    events = [
        *_full_pattern("CUST-WITH", include_feedback=True),
        *_full_pattern("CUST-WITHOUT", include_feedback=False),
    ]
    service = AnalyticsService(FakeRepository(events))

    result = service.rank_customers(
        start_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        enabled_sources=ALL_SOURCES,
    )

    by_customer = {customer.customer_id: customer for customer in result.customers}
    assert by_customer["CUST-WITH"].risk_score == 100
    assert by_customer["CUST-WITHOUT"].risk_score == 80
    feedback_signal = next(
        signal for signal in by_customer["CUST-WITH"].signals if signal.code == "negative_feedback"
    )
    assert feedback_signal.score == 20


def test_negative_feedback_only_counts_after_failure_and_no_later_than_valid_voc():
    events = _full_pattern("CUST-001", include_feedback=False)
    failed_at = min(event.occurred_at for event in events)
    events.extend(
        [
            _event(
                "EVT-CUST-001-00",
                "CUST-001",
                failed_at - timedelta(minutes=1),
                source_id="search_feedback",
                event_type="feedback",
                action="submit_feedback",
                outcome="negative",
            ),
            _event(
                "EVT-CUST-001-05",
                "CUST-001",
                failed_at + timedelta(hours=3),
                source_id="search_feedback",
                event_type="feedback",
                action="submit_feedback",
                outcome="negative",
            ),
        ]
    )
    service = AnalyticsService(FakeRepository(events))

    result = service.rank_customers(
        start_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        enabled_sources=ALL_SOURCES,
    )

    assert result.customers[0].risk_score == 80
    assert "negative_feedback" not in {signal.code for signal in result.customers[0].signals}


def test_policy_scores_and_risk_thresholds_are_exact():
    assert (
        FAILED_SEARCH_SCORE,
        SAME_TOPIC_FAILED_REPEAT_SCORE,
        NEGATIVE_FEEDBACK_SCORE,
        SAME_TOPIC_UNRESOLVED_VOC_SCORE,
    ) == (25, 25, 20, 30)
    assert (MEDIUM_RISK_MIN_SCORE, HIGH_RISK_MIN_SCORE) == (40, 75)
    assert risk_level_for_score(0) == "low"
    assert risk_level_for_score(39) == "low"
    assert risk_level_for_score(40) == "medium"
    assert risk_level_for_score(74) == "medium"
    assert risk_level_for_score(75) == "high"
    assert risk_level_for_score(100) == "high"


@pytest.mark.parametrize("group_by", ["source", "topic", "outcome"])
def test_aggregate_events_is_deterministic_and_counts_filtered_events(
    analytics_service: AnalyticsService,
    group_by: str,
):
    first = analytics_service.aggregate_events(
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=ALL_SOURCES,
        group_by=group_by,
    )
    second = analytics_service.aggregate_events(
        start_at=START_AT.isoformat(),
        end_at=END_AT.isoformat(),
        enabled_sources=ALL_SOURCES,
        group_by=group_by,
    )

    assert first == second
    assert first.result_id == second.result_id
    assert first.group_by == group_by
    assert sum(bucket.event_count for bucket in first.buckets) == 108
    assert first.stats.scanned_rows == 108
    assert first.stats.returned_rows == len(first.buckets)
    assert [bucket.value for bucket in first.buckets] == sorted(
        [bucket.value for bucket in first.buckets],
        key=(
            {"search_history": 0, "search_feedback": 1, "voc": 2}.get
            if group_by == "source"
            else None
        ),
    )
    json.dumps(first.model_dump(mode="json"), ensure_ascii=False)


def test_source_aggregate_has_expected_counts(analytics_service: AnalyticsService):
    result = analytics_service.aggregate_events(
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=ALL_SOURCES,
        group_by="source",
    )

    assert [(bucket.value, bucket.event_count) for bucket in result.buckets] == [
        ("search_history", 54),
        ("search_feedback", 30),
        ("voc", 24),
    ]
    assert all(bucket.customer_count > 0 for bucket in result.buckets)


def test_ranking_is_score_descending_then_customer_id_and_is_limited(
    analytics_service: AnalyticsService,
):
    first = analytics_service.rank_customers(
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=ALL_SOURCES,
        limit=8,
    )
    second = analytics_service.rank_customers(
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=ALL_SOURCES,
        limit=8,
    )

    ordering = [(-customer.risk_score, customer.customer_id) for customer in first.customers]
    assert ordering == sorted(ordering)
    assert len(first.customers) == 8
    assert first.customer_count == 8
    assert first.candidate_count >= first.customer_count
    assert first.result_id == second.result_id
    assert first == second
    assert first.stats == ToolStats(scanned_rows=108, returned_rows=8)


def test_result_ids_change_when_normalized_operation_inputs_or_results_change(
    analytics_service: AnalyticsService,
):
    source = analytics_service.aggregate_events(START_AT, END_AT, ALL_SOURCES, group_by="source")
    topic = analytics_service.aggregate_events(START_AT, END_AT, ALL_SOURCES, group_by="topic")
    ranked = analytics_service.rank_customers(START_AT, END_AT, ALL_SOURCES, limit=3)

    assert source.result_id.startswith("aggregate_events:")
    assert ranked.result_id.startswith("rank_customers:")
    assert len({source.result_id, topic.result_id, ranked.result_id}) == 3


def test_customer_journey_is_chronological_display_safe_and_source_filtered(
    analytics_service: AnalyticsService,
):
    result = analytics_service.get_customer_journey(
        customer_id="CUST-003",
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=["voc", "search_history"],
    )

    assert result.customer_id == "CUST-003"
    assert [event.source_id for event in result.events] == [
        "search_history",
        "search_history",
        "voc",
    ]
    assert result.events == sorted(
        result.events,
        key=lambda event: (event.occurred_at, event.event_id),
    )
    assert result.evidence_ids == [event.evidence_id for event in result.events]
    assert result.stats == ToolStats(scanned_rows=3, returned_rows=3)
    payload = result.model_dump(mode="json")
    assert "canonical_customer_id" not in json.dumps(payload)
    assert "attributes" not in json.dumps(payload)


def test_customer_journey_preserves_repository_not_found_error(
    analytics_service: AnalyticsService,
):
    with pytest.raises(EntityNotFoundError, match="CUST-999"):
        analytics_service.get_customer_journey(
            customer_id="CUST-999",
            start_at=START_AT,
            end_at=END_AT,
            enabled_sources=ALL_SOURCES,
        )


def test_get_evidence_is_masked_in_requested_order_and_preserves_duplicates(
    analytics_service: AnalyticsService,
    repository: DuckDBRepository,
):
    journey = repository.list_events(
        START_AT,
        END_AT,
        ["search_history"],
        customer_id="CUST-003",
    )
    requested = [journey[1].evidence_id, journey[0].evidence_id, journey[1].evidence_id]

    first = analytics_service.get_evidence(requested)
    second = analytics_service.get_evidence(requested)

    assert [record.evidence_id for record in first.records] == requested
    assert first.evidence_ids == requested
    assert first.stats == ToolStats(scanned_rows=3, returned_rows=3)
    assert first.result_id == second.result_id
    for record in first.records:
        payload = record.model_dump(mode="json")
        assert record.masked_customer_id.startswith("CU***")
        assert payload["raw_fields"]["customer_ref"] == record.masked_customer_id
        assert "CUST-" not in json.dumps(payload, ensure_ascii=False)


def test_get_evidence_preserves_repository_not_found_error(analytics_service: AnalyticsService):
    with pytest.raises(EntityNotFoundError, match="EVD-unknown"):
        analytics_service.get_evidence(["EVD-unknown"])


def test_catalog_sources_is_stable_and_reports_scan_and_missing_source_stats(
    analytics_service: AnalyticsService,
):
    first = analytics_service.catalog_sources(START_AT, END_AT)
    second = analytics_service.catalog_sources(START_AT.isoformat(), END_AT.isoformat())

    assert first == second
    assert [source.source_id for source in first.sources] == ALL_SOURCES
    assert first.missing_sources == []
    assert first.stats == ToolStats(scanned_rows=108, returned_rows=3)
    assert first.result_id.startswith("catalog_sources:")


def test_service_loads_each_enabled_source_separately_then_merges():
    events = _full_pattern("CUST-001")
    repository = FakeRepository(list(reversed(events)))
    service = AnalyticsService(repository)

    service.match_journey_pattern(
        start_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        enabled_sources=ALL_SOURCES,
    )

    assert [call["enabled_sources"] for call in repository.list_calls] == [
        ["search_history"],
        ["search_feedback"],
        ["voc"],
    ]
    assert all(call["limit"] == 100 for call in repository.list_calls)


def test_service_rejects_source_with_more_rows_than_repository_read_limit():
    occurred_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    events = [
        _event(
            f"EVT-{index:03d}",
            "CUST-001",
            occurred_at + timedelta(minutes=index),
            source_id="search_history",
            event_type="search",
            action="search",
            outcome="success",
        )
        for index in range(101)
    ]
    repository = FakeRepository(events)
    service = AnalyticsService(repository)

    with pytest.raises(
        AnalyticsDataLimitError,
        match=r"search_history.*101",
    ):
        service.aggregate_events(
            start_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            enabled_sources=["search_history"],
        )

    assert repository.list_calls == []


@pytest.mark.parametrize(
    ("start_at", "end_at"),
    [
        (datetime(2026, 7, 20), END_AT),
        (START_AT, datetime(2026, 8, 19)),
        (END_AT, END_AT),
        (END_AT, START_AT),
        ("not-a-date", END_AT.isoformat()),
    ],
)
def test_scoped_methods_reject_invalid_time_ranges_with_typed_error(
    analytics_service: AnalyticsService,
    start_at,
    end_at,
):
    calls = [
        lambda: analytics_service.catalog_sources(start_at, end_at),
        lambda: analytics_service.aggregate_events(start_at, end_at, ALL_SOURCES),
        lambda: analytics_service.match_journey_pattern(start_at, end_at, ALL_SOURCES),
        lambda: analytics_service.rank_customers(start_at, end_at, ALL_SOURCES),
        lambda: analytics_service.get_customer_journey("CUST-003", start_at, end_at, ALL_SOURCES),
    ]

    for call in calls:
        with pytest.raises(AnalyticsInputError, match="start_at|end_at"):
            call()


@pytest.mark.parametrize(
    "enabled_sources",
    [[], ["voc", "voc"], ["unknown"], "voc", ["voc", 1], ALL_SOURCES + ["voc"]],
)
def test_scoped_event_methods_reject_invalid_source_allowlists(
    analytics_service: AnalyticsService,
    enabled_sources,
):
    calls = [
        lambda: analytics_service.aggregate_events(START_AT, END_AT, enabled_sources),
        lambda: analytics_service.match_journey_pattern(START_AT, END_AT, enabled_sources),
        lambda: analytics_service.rank_customers(START_AT, END_AT, enabled_sources),
        lambda: analytics_service.get_customer_journey(
            "CUST-003", START_AT, END_AT, enabled_sources
        ),
    ]

    for call in calls:
        with pytest.raises(AnalyticsInputError, match="enabled_sources"):
            call()


@pytest.mark.parametrize("limit", [0, 101, True, 1.5, "10"])
def test_bounded_methods_reject_invalid_limits(
    analytics_service: AnalyticsService,
    limit,
):
    calls = [
        lambda: analytics_service.aggregate_events(START_AT, END_AT, ALL_SOURCES, limit=limit),
        lambda: analytics_service.match_journey_pattern(START_AT, END_AT, ALL_SOURCES, limit=limit),
        lambda: analytics_service.rank_customers(START_AT, END_AT, ALL_SOURCES, limit=limit),
        lambda: analytics_service.get_customer_journey(
            "CUST-003", START_AT, END_AT, ALL_SOURCES, limit=limit
        ),
    ]

    for call in calls:
        with pytest.raises(AnalyticsInputError, match="limit"):
            call()


@pytest.mark.parametrize("customer_id", ["", "  ", 3, None])
def test_customer_journey_rejects_blank_or_non_string_customer_id(
    analytics_service: AnalyticsService,
    customer_id,
):
    with pytest.raises(AnalyticsInputError, match="customer_id"):
        analytics_service.get_customer_journey(customer_id, START_AT, END_AT, ALL_SOURCES)


@pytest.mark.parametrize(
    "evidence_ids",
    [[], [""], ["  "], ["EVD-1", 2], "EVD-1", ["EVD-1"] * 101],
)
def test_get_evidence_rejects_invalid_or_unbounded_identifiers(
    analytics_service: AnalyticsService,
    evidence_ids,
):
    with pytest.raises(AnalyticsInputError, match="evidence_ids"):
        analytics_service.get_evidence(evidence_ids)


@pytest.mark.parametrize("group_by", ["customer", "", 1, None])
def test_aggregate_rejects_unknown_grouping(
    analytics_service: AnalyticsService,
    group_by,
):
    with pytest.raises(AnalyticsInputError, match="group_by"):
        analytics_service.aggregate_events(
            START_AT,
            END_AT,
            ALL_SOURCES,
            group_by=group_by,
        )


def test_analytics_models_are_strict_and_json_serializable():
    with pytest.raises(ValidationError):
        ToolStats(scanned_rows="1", returned_rows=1)
    with pytest.raises(ValidationError):
        ToolStats(scanned_rows=1, returned_rows=1, unexpected=True)

    stats = ToolStats(scanned_rows=1, returned_rows=1)
    assert json.loads(stats.model_dump_json()) == {"scanned_rows": 1, "returned_rows": 1}


def test_analytics_source_does_not_reference_evaluation_only_table():
    import customer_signal.analytics as analytics_package

    package_path = Path(inspect.getfile(analytics_package)).parent
    source = "\n".join(path.read_text() for path in sorted(package_path.glob("*.py")))

    assert "ground_truth" not in source.lower()
