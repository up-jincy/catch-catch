from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from customer_signal.agent.contracts import (
    RunRequest,
    UnsupportedClaimError,
    UnsupportedQuestionError,
)
from customer_signal.agent.fixture import FixtureRunner
from customer_signal.analytics.service import AnalyticsService
from customer_signal.data.repository import DuckDBRepository
from customer_signal.mcp_server import create_mcp_server
from customer_signal.runtime.events import RunnerEvent


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
ALL_SOURCES = ["search_history", "search_feedback", "voc"]
WITHOUT_VOC = ["search_history", "search_feedback"]
EXPECTED_MATCHES = [
    "CUST-003",
    "CUST-007",
    "CUST-011",
    "CUST-016",
    "CUST-022",
    "CUST-028",
]
TOOL_NAMES = [
    "catalog_sources",
    "aggregate_events",
    "match_journey_pattern",
    "rank_customers",
    "get_customer_journey",
    "get_evidence",
]


@pytest.fixture
def fixture_runner(repository: DuckDBRepository) -> FixtureRunner:
    service = AnalyticsService(repository)
    return FixtureRunner(create_mcp_server(service))


def _request(*, enabled_sources: list[str] | None = None, question: str | None = None):
    return RunRequest(
        question=question
        or "AI 검색에서 해결하지 못하고 고객센터에 문의한 고객이 몇 명이야?",
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=enabled_sources or ALL_SOURCES,
    )


async def test_fixture_runner_uses_six_real_mcp_tools_and_returns_exact_report(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    outcome = await fixture_runner.run(_request(), emit=events.append)

    report = outcome.report
    assert outcome.agent_mode == "fixture"
    assert report.metrics[0].value == 6
    assert report.metrics[0].result_id.startswith("match_journey_pattern:")
    assert [customer.customer_id for customer in report.ranked_customers] == EXPECTED_MATCHES
    assert report.representative_journeys
    assert report.representative_journey_ids
    assert report.sources_used == ALL_SOURCES
    assert all(finding.evidence_ids for finding in report.findings)
    assert all(recommendation.evidence_ids for recommendation in report.recommendations)

    assert list(outcome.facts.tool_result_ids) == TOOL_NAMES
    assert set(outcome.facts.allowed_customer_ids) >= set(EXPECTED_MATCHES)
    assert set(outcome.facts.allowed_evidence_ids) >= {
        evidence_id
        for customer in report.ranked_customers
        for evidence_id in customer.evidence_ids
    }
    assert (
        report.metrics[0].value
        in outcome.facts.allowed_metric_values_by_result[report.metrics[0].result_id]
    )
    for customer in report.ranked_customers:
        assert outcome.facts.ranked_customer_facts[customer.customer_id] == customer

    expected_event_types = ["plan"]
    for _ in TOOL_NAMES:
        expected_event_types.extend(("tool_started", "tool_completed"))
    expected_event_types.extend(("validating", "result"))
    assert [event.type for event in events] == expected_event_types
    assert [
        event.payload["tool"] for event in events if event.type == "tool_started"
    ] == TOOL_NAMES
    assert [
        event.payload["tool"] for event in events if event.type == "tool_completed"
    ] == TOOL_NAMES


async def test_fixture_trace_contains_only_public_summaries(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    await fixture_runner.run(_request(), emit=events.append)

    completed = [event for event in events if event.type == "tool_completed"]
    for event in completed:
        assert set(event.payload) == {
            "tool",
            "source",
            "count",
            "duration_ms",
            "result_id",
        }
        assert isinstance(event.payload["count"], int)
        assert event.payload["duration_ms"] >= 0
    serialized = json.dumps(
        [event.model_dump(mode="json") for event in events],
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        "raw_fields",
        "records",
        "masked_customer_id",
        "chain_of_thought",
        "reasoning",
    ):
        assert forbidden not in serialized


async def test_fixture_runner_reports_zero_matches_when_voc_is_disabled(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    outcome = await fixture_runner.run(
        _request(enabled_sources=WITHOUT_VOC),
        emit=events.append,
    )

    assert outcome.report.metrics[0].value == 0
    assert outcome.report.ranked_customers == []
    assert outcome.report.representative_journeys
    assert any("voc" in limitation.lower() for limitation in outcome.report.limitations)
    assert [
        event.payload["tool"] for event in events if event.type == "tool_started"
    ] == TOOL_NAMES


async def test_fixture_runner_is_deterministic_for_the_same_request(
    fixture_runner: FixtureRunner,
) -> None:
    first = await fixture_runner.run(_request(), emit=lambda _event: None)
    second = await fixture_runner.run(_request(), emit=lambda _event: None)

    assert first.report == second.report
    assert first.facts == second.facts


@pytest.mark.parametrize(
    "question",
    [
        "검색 실패 뒤 상담 전환 고객을 분석해 줘",
        "고객 지원 센터까지 연결된 이용자는 몇 명이야?",
        "상담원 연결 전 행동을 확인해 줘",
        "문의로 이어진 고객 여정을 알려 줘",
        "다시 찾아본 뒤 VOC를 남긴 고객을 리서치해 줘",
    ],
)
async def test_fixture_runner_recognizes_supported_korean_paraphrases(
    fixture_runner: FixtureRunner,
    question: str,
) -> None:
    outcome = await fixture_runner.run(
        _request(question=question),
        emit=lambda _event: None,
    )

    assert outcome.report.metrics[0].value == 6


async def test_fixture_runner_rejects_unrelated_question(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedQuestionError):
        await fixture_runner.run(
            _request(question="이번 달 신규 가입 매출을 예측해 줘"),
            emit=events.append,
        )

    assert [event.type for event in events] == ["error"]


async def test_fixture_runner_does_not_publish_result_when_validation_fails(
    repository: DuckDBRepository,
) -> None:
    def reject_claims(_report, _facts) -> None:
        raise UnsupportedClaimError("fabricated claim")

    runner = FixtureRunner(
        create_mcp_server(AnalyticsService(repository)),
        validator=reject_claims,
    )
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedClaimError):
        await runner.run(_request(), emit=events.append)

    assert events[-2].type == "validating"
    assert events[-1].type == "error"
    assert all(event.type != "result" for event in events)


@pytest.mark.parametrize(
    "overrides",
    [
        {"question": "   "},
        {"start_at": datetime(2026, 7, 20), "end_at": datetime(2026, 8, 19)},
        {
            "start_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
            "end_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
        },
        {"enabled_sources": []},
        {"enabled_sources": ["search_history", "search_history"]},
        {"enabled_sources": ["search_history", "billing"]},
        {"start_at": 1_780_000_000},
        {"enabled_sources": ("search_history",)},
    ],
)
def test_run_request_rejects_invalid_inputs(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "question": "검색 실패 후 문의 고객 수",
        "start_at": START_AT,
        "end_at": END_AT,
        "enabled_sources": ALL_SOURCES,
    }
    values.update(overrides)

    with pytest.raises(ValidationError):
        RunRequest.model_validate(values)


def test_runner_event_rejects_unsafe_payload() -> None:
    with pytest.raises(ValidationError):
        RunnerEvent(
            type="tool_completed",
            payload={"tool": "get_evidence", "records": [{"secret": "value"}]},
        )
