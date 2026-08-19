from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from customer_signal.domain.models import CustomerEvent, EvidenceRecord
from customer_signal.domain.reports import (
    AnalysisScope,
    Finding,
    InsightReport,
    JourneyEvent,
    Metric,
    RankedCustomer,
    Recommendation,
    Signal,
    SignalContribution,
)
from customer_signal.synthetic.generator import generate_dataset


SEOUL = ZoneInfo("Asia/Seoul")
START_AT = datetime(2026, 7, 20, tzinfo=SEOUL)
END_AT = datetime(2026, 8, 19, tzinfo=SEOUL)
POSITIVE_CUSTOMER_IDS = [
    "CUST-003",
    "CUST-007",
    "CUST-011",
    "CUST-016",
    "CUST-022",
    "CUST-028",
]


def _events_by_customer(dataset) -> dict[str, list[CustomerEvent]]:
    grouped: dict[str, list[CustomerEvent]] = defaultdict(list)
    for event in dataset.events:
        grouped[event.canonical_customer_id].append(event)
    return {
        customer_id: sorted(events, key=lambda event: (event.occurred_at, event.event_id))
        for customer_id, events in grouped.items()
    }


def _positive_sequence(events: list[CustomerEvent]) -> tuple[CustomerEvent, ...] | None:
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_id))
    for failed in ordered:
        if failed.event_type != "search" or failed.outcome != "failed":
            continue

        repeats = [
            event
            for event in ordered
            if event.action == "repeat_search"
            and event.event_type == "search"
            and event.topic == failed.topic
            and failed.occurred_at < event.occurred_at <= failed.occurred_at + timedelta(hours=24)
        ]
        for repeat in repeats:
            feedbacks = [
                event
                for event in ordered
                if event.event_type == "feedback"
                and event.outcome == "negative"
                and event.topic == failed.topic
                and repeat.occurred_at < event.occurred_at
            ]
            for feedback in feedbacks:
                vocs = [
                    event
                    for event in ordered
                    if event.event_type == "voc"
                    and event.outcome == "unresolved"
                    and event.topic == failed.topic
                    and feedback.occurred_at
                    < event.occurred_at
                    <= failed.occurred_at + timedelta(hours=72)
                ]
                if vocs:
                    return failed, repeat, feedback, vocs[0]
    return None


def _classify_near_miss(events: list[CustomerEvent]) -> str:
    failed_searches = [
        event for event in events if event.event_type == "search" and event.outcome == "failed"
    ]
    if not failed_searches:
        return "successful_search"

    first_failed = min(failed_searches, key=lambda event: event.occurred_at)
    valid_repeats = [
        event
        for event in events
        if event.action == "repeat_search"
        and event.topic == first_failed.topic
        and first_failed.occurred_at
        < event.occurred_at
        <= first_failed.occurred_at + timedelta(hours=24)
    ]
    if not valid_repeats:
        return "failed_without_repeat"

    same_topic_vocs = [
        event
        for event in events
        if event.event_type == "voc"
        and event.outcome == "unresolved"
        and event.topic == first_failed.topic
        and event.occurred_at > first_failed.occurred_at
    ]
    if not same_topic_vocs:
        return "failed_repeat_without_voc"
    if min(event.occurred_at for event in same_topic_vocs) > first_failed.occurred_at + timedelta(
        hours=72
    ):
        return "failed_repeat_late_voc"
    return "unexpected_full_pattern"


def test_dataset_is_seeded_and_contains_exact_customers_and_ground_truth():
    first = generate_dataset(seed=20260819)
    second = generate_dataset(seed=20260819)

    assert first.model_dump() == second.model_dump()
    assert first.customers == [f"CUST-{index:03d}" for index in range(1, 31)]
    assert first.ground_truth_customer_ids == POSITIVE_CUSTOMER_IDS
    assert {event.source_id for event in first.events} == {
        "search_history",
        "search_feedback",
        "voc",
    }
    assert generate_dataset(seed=20260820).model_dump() != first.model_dump()


def test_events_are_stably_ordered_inside_the_seoul_time_window():
    dataset = generate_dataset()

    assert dataset.events == sorted(
        dataset.events,
        key=lambda event: (event.occurred_at, event.event_id),
    )
    assert all(START_AT <= event.occurred_at < END_AT for event in dataset.events)
    assert all(event.occurred_at.tzinfo == SEOUL for event in dataset.events)
    assert all(
        re.fullmatch(r"EVT-20260819-\d{3}-\d{2}", event.event_id) for event in dataset.events
    )


def test_positive_customers_have_the_required_ordered_same_topic_sequence():
    events_by_customer = _events_by_customer(generate_dataset())

    for customer_id in POSITIVE_CUSTOMER_IDS:
        sequence = _positive_sequence(events_by_customer[customer_id])
        assert sequence is not None, customer_id
        failed, repeat, feedback, voc = sequence
        assert failed.topic == repeat.topic == feedback.topic == voc.topic
        assert repeat.occurred_at - failed.occurred_at <= timedelta(hours=24)
        assert voc.occurred_at - failed.occurred_at <= timedelta(hours=72)
        assert [event.source_id for event in sequence] == [
            "search_history",
            "search_history",
            "search_feedback",
            "voc",
        ]


def test_other_customers_cover_all_near_misses_without_matching_the_full_pattern():
    dataset = generate_dataset()
    events_by_customer = _events_by_customer(dataset)
    near_miss_ids = [
        customer_id
        for customer_id in dataset.customers
        if customer_id not in dataset.ground_truth_customer_ids
    ]

    assert all(
        _positive_sequence(events_by_customer[customer_id]) is None for customer_id in near_miss_ids
    )
    classifications = {
        _classify_near_miss(events_by_customer[customer_id]) for customer_id in near_miss_ids
    }
    assert classifications == {
        "successful_search",
        "failed_without_repeat",
        "failed_repeat_late_voc",
        "failed_repeat_without_voc",
    }
    assert {event.topic for event in dataset.events} >= {
        "인터넷 장애",
        "로밍",
        "요금",
        "기기 변경",
    }


def test_every_event_has_matching_unique_masked_evidence_without_id_leakage():
    dataset = generate_dataset()
    evidence_by_id = {record.evidence_id: record for record in dataset.evidence}

    assert len(evidence_by_id) == len(dataset.evidence) == len(dataset.events)
    assert set(evidence_by_id) == {event.evidence_id for event in dataset.events}

    for event in dataset.events:
        evidence = evidence_by_id[event.evidence_id]
        assert evidence.source_id == event.source_id
        assert evidence.occurred_at == event.occurred_at
        assert evidence.masked_customer_id != event.canonical_customer_id
        assert "***" in evidence.masked_customer_id
        assert event.canonical_customer_id not in evidence.masked_customer_id
        assert event.canonical_customer_id not in evidence.summary
        assert event.canonical_customer_id not in json.dumps(
            evidence.raw_fields,
            ensure_ascii=False,
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            CustomerEvent,
            {
                "event_id": "EVT-1",
                "evidence_id": "EVD-1",
                "source_id": "search_history",
                "occurred_at": datetime(2026, 8, 1, 12),
                "event_type": "search",
                "action": "search",
                "topic": "요금",
                "outcome": "success",
                "text": "요금 검색",
                "canonical_customer_id": "CUST-001",
            },
        ),
        (
            EvidenceRecord,
            {
                "evidence_id": "EVD-1",
                "source_id": "search_history",
                "occurred_at": datetime(2026, 8, 1, 12),
                "masked_customer_id": "CU***001",
                "summary": "검색 성공",
                "raw_fields": {},
            },
        ),
        (
            JourneyEvent,
            {
                "event_id": "EVT-1",
                "evidence_id": "EVD-1",
                "source_id": "search_history",
                "occurred_at": datetime(2026, 8, 1, 12),
                "event_type": "search",
                "action": "search",
                "topic": "요금",
                "outcome": "success",
                "text": "요금 검색",
            },
        ),
    ],
)
def test_domain_timestamps_reject_naive_datetimes(model, payload):
    with pytest.raises(ValidationError, match="timezone"):
        model.model_validate(payload)


def test_report_contracts_are_framework_independent_json_models():
    occurred_at = datetime(2026, 8, 1, 12, tzinfo=SEOUL)
    signal = Signal(
        code="failed_search",
        label="검색 실패",
        score=25,
        evidence_ids=["EVD-1"],
    )
    report = InsightReport(
        analysis_type="cohort",
        scope=AnalysisScope(
            start_at=START_AT,
            end_at=END_AT,
            enabled_sources=["search_history", "search_feedback", "voc"],
            population_description="최근 30일 검색 고객",
        ),
        headline="6명의 고객이 패턴에 해당합니다.",
        executive_summary="검색 실패 후 미해결 문의로 이어진 고객입니다.",
        metrics=[Metric(label="탐지 고객", value=6, unit="명", result_id="RESULT-1")],
        findings=[
            Finding(
                title="반복 검색",
                description="같은 주제를 다시 검색했습니다.",
                confidence="high",
                evidence_ids=["EVD-1"],
            )
        ],
        signal_contributions=[
            SignalContribution(
                source_id="search_history",
                score=50,
                signals=[signal],
            )
        ],
        ranked_customers=[
            RankedCustomer(
                customer_id="CUST-003",
                risk_score=100,
                risk_level="high",
                signals=[signal],
                evidence_ids=["EVD-1"],
                last_event_at=occurred_at,
            )
        ],
        representative_journeys=[
            JourneyEvent(
                event_id="EVT-1",
                evidence_id="EVD-1",
                source_id="search_history",
                occurred_at=occurred_at,
                event_type="search",
                action="search",
                topic="인터넷 장애",
                outcome="failed",
                text="해결하지 못했습니다.",
            )
        ],
        recommendations=[
            Recommendation(
                action_id="care_call",
                title="Care call",
                reason="미해결 문의가 확인되었습니다.",
                evidence_ids=["EVD-1"],
            )
        ],
        sources_used=["search_history", "search_feedback", "voc"],
    )

    payload = json.loads(report.model_dump_json())
    assert payload["metrics"][0]["value"] == 6
    assert payload["ranked_customers"][0]["risk_score"] == 100
    assert payload["ranked_customers"][0]["signals"][0]["score"] == 25
    assert payload["representative_journeys"][0]["occurred_at"].endswith("+09:00")


def test_cli_serializes_to_stdout_without_writing_by_default(tmp_path, monkeypatch, capsys):
    from customer_signal.synthetic.cli import main

    monkeypatch.chdir(tmp_path)

    assert main(["--seed", "20260819"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["customers"][0] == "CUST-001"
    assert list(tmp_path.iterdir()) == []


def test_cli_writes_json_only_to_an_explicit_output_path(tmp_path, capsys):
    from customer_signal.synthetic.cli import main

    output_path = tmp_path / "dataset.json"

    assert main(["--seed", "20260819", "--output", str(output_path)]) == 0

    assert capsys.readouterr().out == ""
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ground_truth_customer_ids"] == POSITIVE_CUSTOMER_IDS
