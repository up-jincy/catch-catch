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
from customer_signal.analytics.models import CustomerJourneyResult, EvidenceResult
from customer_signal.analytics.service import AnalyticsService
from customer_signal.data.repository import DuckDBRepository
from customer_signal.mcp_server import create_mcp_server
from customer_signal.runtime.events import RunnerEvent


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
EMPTY_START_AT = "2027-01-01T00:00:00+09:00"
EMPTY_END_AT = "2027-01-02T00:00:00+09:00"
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
        question=question or "AI 검색에서 해결하지 못하고 고객센터에 문의한 고객이 몇 명이야?",
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=enabled_sources or ALL_SOURCES,
    )


class _TamperedEvidenceRunner(FixtureRunner):
    def __init__(self, server, *, tamper: str) -> None:
        super().__init__(server)
        self._tamper = tamper

    async def _call_tool(self, client, **kwargs):
        result = await super()._call_tool(client, **kwargs)
        if not isinstance(result, EvidenceResult):
            return result
        record = result.records[0]
        if self._tamper == "id":
            fabricated_id = "EVD-FABRICATED"
            return result.model_copy(
                update={
                    "evidence_ids": [fabricated_id],
                    "records": [record.model_copy(update={"evidence_id": fabricated_id})],
                }
            )
        mismatched_source = "voc" if record.source_id != "voc" else "search_history"
        return result.model_copy(
            update={"records": [record.model_copy(update={"source_id": mismatched_source})]}
        )


class _DuplicateResultRunner(FixtureRunner):
    def __init__(self, server) -> None:
        super().__init__(server)
        self._match_result_id: str | None = None

    async def _call_tool(self, client, **kwargs):
        result = await super()._call_tool(client, **kwargs)
        if kwargs["name"] == "match_journey_pattern":
            self._match_result_id = result.result_id
        if kwargs["name"] == "rank_customers":
            assert self._match_result_id is not None
            return result.model_copy(update={"result_id": self._match_result_id})
        return result


class _TamperedJourneyRunner(FixtureRunner):
    def __init__(self, server, *, tamper: str) -> None:
        super().__init__(server)
        self._tamper = tamper

    async def _call_tool(self, client, **kwargs):
        result = await super()._call_tool(client, **kwargs)
        if not isinstance(result, CustomerJourneyResult):
            return result
        if self._tamper == "customer_id":
            return result.model_copy(update={"customer_id": "CUST-FABRICATED"})
        fabricated_ids = [
            f"EVD-NOT-REPRESENTATIVE-{index}" for index, _event in enumerate(result.events)
        ]
        return result.model_copy(
            update={
                "evidence_ids": fabricated_ids,
                "events": [
                    event.model_copy(update={"evidence_id": evidence_id})
                    for event, evidence_id in zip(result.events, fabricated_ids, strict=True)
                ],
            }
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
        evidence_id for customer in report.ranked_customers for evidence_id in customer.evidence_ids
    }
    assert any(
        report.metrics[0].label == supported.label
        and report.metrics[0].value == supported.value
        and report.metrics[0].unit == supported.unit
        for supported in outcome.facts.allowed_metrics_by_result[report.metrics[0].result_id]
    )
    for customer in report.ranked_customers:
        assert outcome.facts.ranked_customer_facts[customer.customer_id] == customer

    expected_event_types = ["plan"]
    for _ in TOOL_NAMES:
        expected_event_types.extend(("tool_started", "tool_completed"))
    expected_event_types.extend(("validating", "result"))
    assert [event.type for event in events] == expected_event_types
    assert [event.payload["tool"] for event in events if event.type == "tool_started"] == TOOL_NAMES
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


async def test_catalog_trace_does_not_claim_requested_sources(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    await fixture_runner.run(_request(), emit=events.append)

    catalog_started = next(
        event
        for event in events
        if event.type == "tool_started" and event.payload["tool"] == "catalog_sources"
    )
    catalog_completed = next(
        event
        for event in events
        if event.type == "tool_completed" and event.payload["tool"] == "catalog_sources"
    )
    assert catalog_started.payload["source"] == []
    assert catalog_completed.payload["source"] == []


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
    assert [event.payload["tool"] for event in events if event.type == "tool_started"] == TOOL_NAMES


async def test_fixture_runner_completes_empty_range_without_inventing_representative_data(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []
    request = RunRequest(
        question="검색 실패 후 문의 고객을 확인해 줘",
        start_at=EMPTY_START_AT,
        end_at=EMPTY_END_AT,
        enabled_sources=ALL_SOURCES,
    )

    outcome = await fixture_runner.run(request, emit=events.append)

    assert outcome.report.metrics[0].value == 0
    assert outcome.report.ranked_customers == []
    assert outcome.report.representative_journeys == []
    assert outcome.report.representative_journey_ids == []
    assert outcome.report.findings == []
    assert outcome.report.recommendations == []
    assert outcome.report.sources_used == []
    assert any("데이터" in limitation for limitation in outcome.report.limitations)
    assert set(outcome.facts.allowed_sources) == set(ALL_SOURCES)
    assert outcome.facts.allowed_customer_ids == frozenset()
    assert outcome.facts.allowed_evidence_ids == frozenset()
    assert outcome.facts.fetched_evidence_ids == frozenset()
    assert list(outcome.facts.tool_result_ids) == TOOL_NAMES[:4]

    expected_event_types = ["plan"]
    for _ in TOOL_NAMES[:4]:
        expected_event_types.extend(("tool_started", "tool_completed"))
    expected_event_types.extend(("validating", "result"))
    assert [event.type for event in events] == expected_event_types


async def test_fixture_runner_completes_when_present_data_has_no_customer_candidate(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    outcome = await fixture_runner.run(
        _request(enabled_sources=["voc"]),
        emit=events.append,
    )

    assert outcome.report.metrics[0].value == 0
    assert outcome.report.representative_journeys == []
    assert outcome.report.recommendations == []
    assert any("후보" in limitation for limitation in outcome.report.limitations)
    assert [
        event.payload["tool"] for event in events if event.type == "tool_started"
    ] == TOOL_NAMES[:4]


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
        "AI 검색으로 답을 찾지 못해 고객 지원 센터에 연결된 이용자는 몇 명이야?",
        "검색 결과로 문제가 풀리지 않아 상담원에게 문의한 고객 여정을 알려 줘",
        "AI검색에서 미해결된 뒤 VOC를 접수한 고객을 리서치해 줘",
        "다시 검색했지만 해결되지 않아 고객센터까지 간 고객은?",
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


@pytest.mark.parametrize(
    "question",
    [
        "검색 성공 고객은 몇 명이야?",
        "AI 검색에서 성공하거나 실패한 뒤 고객센터에 문의한 고객을 알려 줘",
        "AI 검색에서 해결하고 고객센터에 문의한 고객이 몇 명이야?",
    ],
)
async def test_fixture_runner_rejects_opposite_resolved_intent(
    fixture_runner: FixtureRunner,
    question: str,
) -> None:
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedQuestionError):
        await fixture_runner.run(
            _request(question=question),
            emit=events.append,
        )

    assert [event.type for event in events] == ["error"]


@pytest.mark.parametrize(
    "question",
    [
        "AI 검색 실패 고객은 몇 명이야?",
        "고객센터에 문의한 고객은 몇 명이야?",
    ],
)
async def test_fixture_runner_requires_both_failed_search_and_contact_transition(
    fixture_runner: FixtureRunner,
    question: str,
) -> None:
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedQuestionError):
        await fixture_runner.run(
            _request(question=question),
            emit=events.append,
        )

    assert [event.type for event in events] == ["error"]


async def test_fixture_runner_rejects_a_different_scenario_wrapped_in_supported_terms(
    fixture_runner: FixtureRunner,
) -> None:
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedQuestionError):
        await fixture_runner.run(
            _request(question="검색 실패 후 상담 전환 고객의 신규 가입 매출을 예측해 줘"),
            emit=events.append,
        )

    assert [event.type for event in events] == ["error"]


@pytest.mark.parametrize(
    "question",
    [
        "검색 실패 후 문의한 고객의 평균 나이는?",
        "검색 실패 후 문의한 고객의 주소를 알려 줘",
        "검색 실패 후 상담한 고객의 전화번호를 알려 줘",
        "검색 실패 후 고객센터에 문의한 고객의 수익은 얼마야?",
    ],
)
async def test_fixture_runner_rejects_attribute_pivots_wrapped_in_supported_terms(
    fixture_runner: FixtureRunner,
    question: str,
) -> None:
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedQuestionError):
        await fixture_runner.run(
            _request(question=question),
            emit=events.append,
        )

    assert [event.type for event in events] == ["error"]


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


@pytest.mark.parametrize("tamper", ["id", "source"])
async def test_fixture_runner_rejects_malformed_selected_evidence_provenance(
    repository: DuckDBRepository,
    tamper: str,
) -> None:
    runner = _TamperedEvidenceRunner(
        create_mcp_server(AnalyticsService(repository)),
        tamper=tamper,
    )
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedClaimError, match="Evidence"):
        await runner.run(_request(), emit=events.append)

    assert events[-1].type == "error"
    assert all(event.type != "result" for event in events)


@pytest.mark.parametrize("tamper", ["customer_id", "evidence_intersection"])
async def test_fixture_runner_binds_journey_to_representative_customer_and_evidence(
    repository: DuckDBRepository,
    tamper: str,
) -> None:
    runner = _TamperedJourneyRunner(
        create_mcp_server(AnalyticsService(repository)),
        tamper=tamper,
    )
    events: list[RunnerEvent] = []

    with pytest.raises(UnsupportedClaimError, match="대표 고객|Evidence"):
        await runner.run(_request(), emit=events.append)

    assert [
        event.payload["tool"] for event in events if event.type == "tool_started"
    ] == TOOL_NAMES[:5]
    assert events[-1].type == "error"
    assert all(event.type != "result" for event in events)


async def test_fixture_runner_rejects_duplicate_tool_result_ids(
    repository: DuckDBRepository,
) -> None:
    runner = _DuplicateResultRunner(create_mcp_server(AnalyticsService(repository)))
    events: list[RunnerEvent] = []

    with pytest.raises(ValidationError, match="result_id"):
        await runner.run(_request(), emit=events.append)

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
