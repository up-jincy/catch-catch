from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from customer_signal.domain.models import CustomerEvent, EvidenceRecord, SyntheticDataset
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
ALL_SOURCE_IDS = {
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
}


def _dataset_payload() -> dict:
    return generate_dataset().model_dump()


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
    assert {event.source_id for event in first.events} == ALL_SOURCE_IDS
    assert generate_dataset(seed=20260820).model_dump() != first.model_dump()


def test_dataset_contains_distinct_generic_analysis_patterns() -> None:
    dataset = generate_dataset(seed=20260819)
    events_by_customer = _events_by_customer(dataset)
    negative_pricing_feedback = [
        event
        for event in dataset.events
        if event.event_type == "feedback"
        and event.outcome == "negative"
        and event.topic == "요금제 변경"
    ]
    signup_started = {
        event.canonical_customer_id
        for event in dataset.events
        if event.source_id == "subscription" and event.topic == "가입" and event.action == "started"
    }
    signup_completed = {
        event.canonical_customer_id
        for event in dataset.events
        if event.source_id == "subscription"
        and event.topic == "가입"
        and event.action == "completed"
    }

    assert len(negative_pricing_feedback) == 6
    assert len({event.canonical_customer_id for event in negative_pricing_feedback}) == 6
    assert (
        sum(
            _positive_sequence(events_by_customer[customer_id]) is not None
            for customer_id in dataset.customers
        )
        == 6
    )
    assert len(signup_started) == 12
    assert len(signup_completed) == 7
    assert len(signup_started - signup_completed) == 5


def test_dataset_normalizes_dimensions_and_measures_without_removing_legacy_attributes() -> None:
    events = generate_dataset(seed=20260819).events

    for measure_name in ("rating", "result_count", "session_depth"):
        measured_events = [event for event in events if measure_name in event.attributes]
        assert measured_events
        assert all(
            event.measures[measure_name] == event.attributes[measure_name]
            for event in measured_events
        )

    signup_started = next(
        event
        for event in events
        if event.source_id == "subscription" and event.topic == "가입" and event.action == "started"
    )
    assert signup_started.attributes["stage"] == "application"
    assert signup_started.dimensions["stage"] == "application"


def test_dataset_exposes_all_five_customer_journey_source_families():
    dataset = generate_dataset(seed=20260819)

    assert {event.source_id for event in dataset.events} == ALL_SOURCE_IDS
    assert all(
        {event.source_id for event in dataset.events if event.canonical_customer_id == customer_id}
        == ALL_SOURCE_IDS
        for customer_id in dataset.customers
    )


def test_every_event_identity_reaches_its_canonical_customer_with_explicit_provenance():
    payload = generate_dataset(seed=20260819).model_dump(mode="python")
    edges = payload.get("identity_edges")

    assert edges, "the synthetic dataset must include an explicit identity graph"
    assert {edge["link_type"] for edge in edges} == {"EXACT", "DECLARED", "SYNTHETIC"}
    assert all(edge["provenance"] for edge in edges)

    graph: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for edge in edges:
        left = (edge["left"]["namespace"], edge["left"]["value"])
        right = (edge["right"]["namespace"], edge["right"]["value"])
        graph[left].add(right)
        graph[right].add(left)

    def reaches_canonical(identity: dict, customer_id: str) -> bool:
        start = (identity["namespace"], identity["value"])
        target = ("canonical_customer", customer_id)
        pending = [start]
        visited: set[tuple[str, str]] = set()
        while pending:
            node = pending.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            pending.extend(graph[node] - visited)
        return False

    for event in payload["events"]:
        assert event.get("identities"), event["event_id"]
        assert any(
            reaches_canonical(identity, event["canonical_customer_id"])
            for identity in event["identities"]
        ), event["event_id"]


@pytest.mark.parametrize("seed", [-1, 100_000_000])
def test_generate_dataset_rejects_seed_outside_stable_id_range(seed):
    with pytest.raises(ValueError, match="seed must be between 0 and 99999999"):
        generate_dataset(seed=seed)


@pytest.mark.parametrize("seed", [True, 1.5, "20260819"])
def test_generate_dataset_rejects_non_integer_seed(seed):
    with pytest.raises(TypeError, match="seed must be an integer"):
        generate_dataset(seed=seed)


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
    ("duplicate", "message"),
    [
        ("customer", "customers must be unique"),
        ("ground_truth", "ground_truth_customer_ids must be unique"),
        ("event_id", "event_id values must be unique"),
        ("event_evidence_id", "event evidence_id references must be unique"),
        ("evidence_id", "evidence_id values must be unique"),
    ],
)
def test_dataset_rejects_duplicate_identity_fields(duplicate, message):
    payload = _dataset_payload()
    if duplicate == "customer":
        payload["customers"].append(payload["customers"][0])
    elif duplicate == "ground_truth":
        payload["ground_truth_customer_ids"].append(payload["ground_truth_customer_ids"][0])
    elif duplicate == "event_id":
        payload["events"][1]["event_id"] = payload["events"][0]["event_id"]
    elif duplicate == "event_evidence_id":
        payload["events"][1]["evidence_id"] = payload["events"][0]["evidence_id"]
    else:
        payload["evidence"][1]["evidence_id"] = payload["evidence"][0]["evidence_id"]

    with pytest.raises(ValidationError, match=message):
        SyntheticDataset.model_validate(payload)


@pytest.mark.parametrize(
    ("invalid_relation", "message"),
    [
        ("unknown_ground_truth", "ground truth customers must belong to customers"),
        ("unknown_event_customer", "event customers must belong to customers"),
        ("missing_evidence", "every event evidence_id must exist"),
        ("orphan_evidence", "evidence records must not be orphaned"),
    ],
)
def test_dataset_rejects_invalid_membership_and_evidence_relations(invalid_relation, message):
    payload = _dataset_payload()
    if invalid_relation == "unknown_ground_truth":
        payload["ground_truth_customer_ids"].append("CUST-999")
    elif invalid_relation == "unknown_event_customer":
        payload["events"][0]["canonical_customer_id"] = "CUST-999"
    elif invalid_relation == "missing_evidence":
        payload["events"][0]["evidence_id"] = "EVD-MISSING"
    else:
        orphan = payload["evidence"][0].copy()
        orphan["evidence_id"] = "EVD-ORPHAN"
        payload["evidence"].append(orphan)

    with pytest.raises(ValidationError, match=message):
        SyntheticDataset.model_validate(payload)


@pytest.mark.parametrize(
    ("misalignment", "message"),
    [
        ("source", "event and evidence source_id must align"),
        ("timestamp", "event and evidence occurred_at must align"),
    ],
)
def test_dataset_rejects_misaligned_event_evidence_pairs(misalignment, message):
    payload = _dataset_payload()
    evidence_id = payload["events"][0]["evidence_id"]
    evidence = next(
        record for record in payload["evidence"] if record["evidence_id"] == evidence_id
    )
    if misalignment == "source":
        evidence["source_id"] = (
            "voc" if payload["events"][0]["source_id"] != "voc" else "search_history"
        )
    else:
        evidence["occurred_at"] += timedelta(seconds=1)

    with pytest.raises(ValidationError, match=message):
        SyntheticDataset.model_validate(payload)


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


@pytest.mark.parametrize("end_at", [START_AT, START_AT - timedelta(seconds=1)])
def test_analysis_scope_requires_strictly_increasing_time_bounds(end_at):
    with pytest.raises(ValidationError, match="start_at must be before end_at"):
        AnalysisScope(
            start_at=START_AT,
            end_at=end_at,
            enabled_sources=["search_history"],
            population_description="검색 고객",
        )


@pytest.mark.parametrize("naive_field", ["start_at", "end_at"])
def test_analysis_scope_rejects_naive_time_bounds(naive_field):
    payload = {
        "start_at": START_AT,
        "end_at": END_AT,
        "enabled_sources": ["search_history"],
        "population_description": "검색 고객",
    }
    payload[naive_field] = payload[naive_field].replace(tzinfo=None)

    with pytest.raises(ValidationError, match="timezone"):
        AnalysisScope.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "score_field", "payload"),
    [
        (
            Signal,
            "score",
            {"code": "failed_search", "label": "검색 실패", "evidence_ids": []},
        ),
        (
            SignalContribution,
            "score",
            {"source_id": "search_history", "signals": []},
        ),
        (
            RankedCustomer,
            "risk_score",
            {
                "customer_id": "CUST-003",
                "risk_level": "high",
                "signals": [],
                "evidence_ids": [],
            },
        ),
    ],
)
@pytest.mark.parametrize("invalid_score", [-1, 101, float("nan"), float("inf"), float("-inf")])
def test_report_scores_reject_out_of_range_and_non_finite_values(
    model,
    score_field,
    payload,
    invalid_score,
):
    payload[score_field] = invalid_score

    with pytest.raises(ValidationError) as error:
        model.model_validate(payload)

    assert error.value.errors()[0]["loc"] == (score_field,)


@pytest.mark.parametrize(
    ("model", "score_field", "payload"),
    [
        (Signal, "score", {"code": "failed_search", "label": "검색 실패"}),
        (SignalContribution, "score", {"source_id": "search_history"}),
        (
            RankedCustomer,
            "risk_score",
            {"customer_id": "CUST-003", "risk_level": "high"},
        ),
    ],
)
@pytest.mark.parametrize("boundary", [0, 100])
def test_report_scores_accept_inclusive_boundaries(model, score_field, payload, boundary):
    payload[score_field] = boundary

    assert getattr(model.model_validate(payload), score_field) == boundary


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_metric_rejects_non_finite_numeric_values(invalid_value):
    with pytest.raises(ValidationError):
        Metric(label="비율", value=invalid_value, result_id="RESULT-1")


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
