from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import pytest
from deepagents import create_deep_agent
from fastapi.testclient import TestClient
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult
from pydantic import Field

from customer_signal.agent.contracts import RunRequest, RunnerOutcome
from customer_signal.agent.fixture import FixtureRunner
from customer_signal.agent.gemini import GeminiRunner, GeminiRunnerError
from customer_signal.analytics.service import AnalyticsService
from customer_signal.api import create_app
from customer_signal.config import Settings
from customer_signal.data.repository import DuckDBRepository
from customer_signal.domain.reports import InsightReport
from customer_signal.mcp_server import create_mcp_server
from customer_signal.runtime.coordinator import RunCoordinator
from customer_signal.runtime.events import RunnerEvent
from customer_signal.runtime.run_store import RunStore


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
ALL_SOURCES = ["search_history", "search_feedback", "voc"]
PRIMARY_MODEL = "gemini-3.7-flash"
FALLBACK_MODEL = "gemini-3.6-flash"


def _request(*, enabled_sources: list[str] | None = None) -> RunRequest:
    return RunRequest(
        question="AI 검색에서 해결하지 못하고 고객센터에 문의한 고객이 몇 명이야?",
        start_at=START_AT,
        end_at=END_AT,
        enabled_sources=enabled_sources if enabled_sources is not None else ALL_SOURCES,
    )


@dataclass(frozen=True)
class _PreparedAnalysis:
    fixture_outcome: RunnerOutcome
    report: InsightReport
    calls: list[tuple[str, dict[str, Any], CallToolResult]]


@pytest.fixture
async def prepared_analysis(repository: DuckDBRepository) -> _PreparedAnalysis:
    request = _request()
    service = AnalyticsService(repository)
    fixture_outcome = await FixtureRunner(create_mcp_server(service)).run(
        request,
        emit=lambda _event: None,
    )
    scope = {
        "start_at": request.start_at.isoformat(),
        "end_at": request.end_at.isoformat(),
        "enabled_sources": list(request.enabled_sources),
    }
    catalog = service.catalog_sources(request.start_at, request.end_at)
    aggregate = service.aggregate_events(
        request.start_at,
        request.end_at,
        request.enabled_sources,
        group_by="topic",
    )
    matched = service.match_journey_pattern(
        request.start_at,
        request.end_at,
        request.enabled_sources,
    )
    ranked = service.rank_customers(
        request.start_at,
        request.end_at,
        request.enabled_sources,
    )
    representative = matched.customers[0]
    journey = service.get_customer_journey(
        representative.customer_id,
        request.start_at,
        request.end_at,
        request.enabled_sources,
    )
    selected_evidence_id = next(
        evidence_id
        for evidence_id in reversed(journey.evidence_ids)
        if evidence_id in representative.evidence_ids
    )
    evidence = service.get_evidence([selected_evidence_id])
    typed_calls = [
        (
            "catalog_sources",
            {"start_at": scope["start_at"], "end_at": scope["end_at"]},
            catalog,
        ),
        (
            "aggregate_events",
            {**scope, "group_by": "topic"},
            aggregate,
        ),
        ("match_journey_pattern", scope, matched),
        ("rank_customers", scope, ranked),
        (
            "get_customer_journey",
            {**scope, "customer_id": representative.customer_id},
            journey,
        ),
        ("get_evidence", {"evidence_ids": [selected_evidence_id]}, evidence),
    ]
    calls = [
        (
            name,
            arguments,
            CallToolResult(
                content=[],
                structuredContent=result.model_dump(mode="json"),
            ),
        )
        for name, arguments, result in typed_calls
    ]
    return _PreparedAnalysis(
        fixture_outcome=fixture_outcome,
        report=fixture_outcome.report,
        calls=calls,
    )


class _FakeMcpClient:
    def __init__(self, factory: _FakeMcpClientFactory) -> None:
        self._factory = factory

    async def get_tools(self) -> list[object]:
        self._factory.get_tools_calls += 1
        return [object()]


class _FakeMcpClientFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.interceptors: list[Any] = []
        self.get_tools_calls = 0

    def __call__(
        self,
        connections: dict[str, Any],
        *,
        tool_interceptors: list[Any],
        **kwargs: Any,
    ) -> _FakeMcpClient:
        self.calls.append(
            {
                "connections": connections,
                "tool_interceptors": tool_interceptors,
                **kwargs,
            }
        )
        self.interceptors = tool_interceptors
        return _FakeMcpClient(self)


@dataclass(frozen=True)
class _FakeModel:
    name: str


class _FakeModelFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> _FakeModel:
        self.calls.append(kwargs)
        return _FakeModel(name=kwargs["model"])


class _ModelNotFoundError(RuntimeError):
    code = 404
    status = "NOT_FOUND"


class _ReplayAgent:
    def __init__(
        self,
        *,
        interceptor: Any,
        calls: list[tuple[str, dict[str, Any], CallToolResult]],
        report: InsightReport,
        fail_before_tools: Exception | None = None,
        fail_after_tools: int | None = None,
    ) -> None:
        self._interceptor = interceptor
        self._calls = calls
        self._report = report
        self._fail_before_tools = fail_before_tools
        self._fail_after_tools = fail_after_tools

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        assert state["messages"][0]["role"] == "user"
        if self._fail_before_tools is not None:
            raise self._fail_before_tools
        for index, (name, arguments, result) in enumerate(self._calls, start=1):
            request = MCPToolCallRequest(
                name=name,
                args=arguments,
                server_name="customer_signal",
            )

            async def handler(
                _request: MCPToolCallRequest,
                response: CallToolResult = result,
            ) -> CallToolResult:
                return response

            await self._interceptor(request, handler)
            if self._fail_after_tools == index:
                raise _ModelNotFoundError("private provider text must not escape")
        return {
            "structured_response": self._report.model_dump(mode="json"),
            "messages": [{"private": "reasoning and provider transcript"}],
        }


class _FakeAgentFactory:
    def __init__(
        self,
        *,
        mcp_factory: _FakeMcpClientFactory,
        prepared: _PreparedAnalysis,
        primary_error: Exception | None = None,
        fail_after_tools: int | None = None,
    ) -> None:
        self._mcp_factory = mcp_factory
        self._prepared = prepared
        self._primary_error = primary_error
        self._fail_after_tools = fail_after_tools
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> _ReplayAgent:
        self.calls.append(kwargs)
        model = kwargs["model"]
        assert isinstance(model, _FakeModel)
        return _ReplayAgent(
            interceptor=self._mcp_factory.interceptors[0],
            calls=self._prepared.calls,
            report=self._prepared.report,
            fail_before_tools=(self._primary_error if model.name == PRIMARY_MODEL else None),
            fail_after_tools=(self._fail_after_tools if model.name == PRIMARY_MODEL else None),
        )


def _runner(
    prepared: _PreparedAnalysis,
    *,
    primary_error: Exception | None = None,
    fail_after_tools: int | None = None,
) -> tuple[GeminiRunner, _FakeMcpClientFactory, _FakeModelFactory, _FakeAgentFactory]:
    mcp_factory = _FakeMcpClientFactory()
    model_factory = _FakeModelFactory()
    agent_factory = _FakeAgentFactory(
        mcp_factory=mcp_factory,
        prepared=prepared,
        primary_error=primary_error,
        fail_after_tools=fail_after_tools,
    )
    runner = GeminiRunner(
        api_key="test-gemini-key",
        mcp_url="http://127.0.0.1:8000/mcp/",
        primary_model=PRIMARY_MODEL,
        fallback_model=FALLBACK_MODEL,
        mcp_client_factory=mcp_factory,
        model_factory=model_factory,
        agent_factory=agent_factory,
    )
    return runner, mcp_factory, model_factory, agent_factory


async def test_gemini_runner_lazily_builds_one_structured_agent_and_captures_facts(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    runner, mcp_factory, model_factory, agent_factory = _runner(prepared_analysis)
    first_events: list[RunnerEvent] = []
    second_events: list[RunnerEvent] = []

    assert mcp_factory.calls == []
    first = await runner.run(_request(), emit=first_events.append)
    second = await runner.run(_request(), emit=second_events.append)

    assert first.agent_mode == second.agent_mode == "gemini"
    assert first.report == prepared_analysis.report
    assert list(first.facts.tool_result_ids) == [
        "catalog_sources",
        "aggregate_events",
        "match_journey_pattern",
        "rank_customers",
        "get_customer_journey",
        "get_evidence",
    ]
    assert len(mcp_factory.calls) == 1
    assert mcp_factory.calls[0]["connections"] == {
        "customer_signal": {
            "transport": "streamable_http",
            "url": "http://127.0.0.1:8000/mcp/",
        }
    }
    assert mcp_factory.get_tools_calls == 1
    assert [call["model"] for call in model_factory.calls] == [PRIMARY_MODEL]
    assert model_factory.calls[0]["api_key"] == "test-gemini-key"
    assert model_factory.calls[0]["include_thoughts"] is False
    assert model_factory.calls[0]["request_timeout"] == 40
    assert len(agent_factory.calls) == 1
    assert agent_factory.calls[0]["response_format"] is InsightReport
    assert any(
        isinstance(middleware, TodoListMiddleware)
        for middleware in agent_factory.calls[0]["middleware"]
    )

    expected_types = ["plan"]
    for _ in prepared_analysis.calls:
        expected_types.extend(("tool_started", "tool_completed"))
    expected_types.extend(("validating", "result"))
    assert [event.type for event in first_events] == expected_types
    assert [event.type for event in second_events] == expected_types
    assert first_events[-1].payload["agent_mode"] == "gemini"
    public_trace = json.dumps(
        [event.model_dump(mode="json") for event in first_events],
        ensure_ascii=False,
    ).lower()
    for forbidden in ("reasoning", "messages", "provider transcript", "test-gemini-key"):
        assert forbidden not in public_trace


class _CapturingGoogleModel(BaseChatModel):
    visible_tool_names: set[str] = Field(default_factory=set)

    @property
    def _llm_type(self) -> str:
        return "chat-google-generative-ai"

    def _get_ls_params(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "ls_provider": "google_genai",
            "ls_model_name": PRIMARY_MODEL,
            "ls_model_type": "chat",
        }

    def bind_tools(self, tools: Any, **kwargs: Any) -> _CapturingGoogleModel:
        self.visible_tool_names = {tool.name for tool in tools}
        return self

    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def _real_tool_stub() -> str:
    return "ok"


async def test_real_deep_agent_exposes_only_mcp_tools_and_write_todos(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    # Exercise the production registration path twice before inspecting the installed graph.
    for _ in range(2):
        runner, _mcp_factory, _model_factory, _agent_factory = _runner(prepared_analysis)
        await runner._get_agent(PRIMARY_MODEL)

    model = _CapturingGoogleModel()
    mcp_tools = [
        StructuredTool.from_function(
            _real_tool_stub,
            name=name,
            description=name,
        )
        for name in (
            "catalog_sources",
            "aggregate_events",
            "match_journey_pattern",
            "rank_customers",
            "get_customer_journey",
            "get_evidence",
        )
    ]
    graph = create_deep_agent(
        model=model,
        tools=mcp_tools,
        middleware=[TodoListMiddleware()],
    )

    await graph.ainvoke({"messages": [{"role": "user", "content": "finish"}]})

    assert model.visible_tool_names == {
        "write_todos",
        "catalog_sources",
        "aggregate_events",
        "match_journey_pattern",
        "rank_customers",
        "get_customer_journey",
        "get_evidence",
    }


async def test_model_not_found_before_tools_retries_only_the_configured_fallback_model(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    runner, _mcp_factory, model_factory, _agent_factory = _runner(
        prepared_analysis,
        primary_error=_ModelNotFoundError("private primary model response"),
    )
    events: list[RunnerEvent] = []

    outcome = await runner.run(_request(), emit=events.append)

    assert outcome.agent_mode == "gemini"
    assert [call["model"] for call in model_factory.calls] == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert "error" not in [event.type for event in events]
    assert "fallback" not in [event.type for event in events]


async def test_model_not_found_after_a_tool_call_does_not_retry_or_publish_provider_text(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    runner, _mcp_factory, model_factory, _agent_factory = _runner(
        prepared_analysis,
        fail_after_tools=1,
    )
    events: list[RunnerEvent] = []

    with pytest.raises(GeminiRunnerError) as caught:
        await runner.run(_request(), emit=events.append)

    assert caught.value.code == "gemini_model_not_found"
    assert "private" not in str(caught.value).lower()
    assert [call["model"] for call in model_factory.calls] == [PRIMARY_MODEL]
    assert [event.type for event in events] == ["plan", "tool_started", "tool_completed"]


async def test_duplicate_mcp_tool_call_is_rejected_before_the_second_request(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    duplicate = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=prepared_analysis.report,
        calls=[prepared_analysis.calls[0], prepared_analysis.calls[0]],
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(duplicate)
    events: list[RunnerEvent] = []

    with pytest.raises(GeminiRunnerError) as caught:
        await runner.run(_request(), emit=events.append)

    assert caught.value.code == "gemini_tool_policy_failed"
    assert [event.type for event in events] == ["plan", "tool_started", "tool_completed"]


async def test_bounded_mcp_tools_accept_only_omitted_or_exact_integer_100_limits(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    bounded_tools = {
        "aggregate_events",
        "match_journey_pattern",
        "rank_customers",
        "get_customer_journey",
    }
    for tool_name in bounded_tools:
        for invalid_limit in (1, "100", True):
            tampered_calls = [
                (
                    name,
                    {**arguments, "limit": invalid_limit} if name == tool_name else dict(arguments),
                    response,
                )
                for name, arguments, response in prepared_analysis.calls
            ]
            tampered = _PreparedAnalysis(
                fixture_outcome=prepared_analysis.fixture_outcome,
                report=prepared_analysis.report,
                calls=tampered_calls,
            )
            runner, _mcp_factory, _model_factory, _agent_factory = _runner(tampered)
            events: list[RunnerEvent] = []

            with pytest.raises(GeminiRunnerError) as caught:
                await runner.run(_request(), emit=events.append)

            assert caught.value.code == "gemini_tool_policy_failed"
            assert tool_name not in [
                event.payload["tool"] for event in events if event.type == "tool_started"
            ]

    exact_limit_calls = [
        (
            name,
            {**arguments, "limit": 100} if name in bounded_tools else dict(arguments),
            response,
        )
        for name, arguments, response in prepared_analysis.calls
    ]
    exact = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=prepared_analysis.report,
        calls=exact_limit_calls,
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(exact)

    outcome = await runner.run(_request(), emit=lambda _event: None)

    assert outcome.agent_mode == "gemini"


def _empty_fabricated_draft(report: InsightReport) -> InsightReport:
    draft = report.model_copy(deep=True)
    draft.headline = "검색 실패 후 문의로 이어진 고객 42명"
    draft.executive_summary = "근거 없이 42명이라고 주장합니다."
    draft.metrics = []
    draft.findings = []
    draft.signal_contributions = []
    draft.ranked_customers = []
    draft.representative_journeys = []
    draft.representative_journey_ids = []
    draft.recommendations = []
    draft.sources_used = []
    draft.limitations = []
    return draft


async def test_positive_run_rebuilds_an_empty_fabricated_draft_from_mcp_results(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    fabricated = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=_empty_fabricated_draft(prepared_analysis.report),
        calls=prepared_analysis.calls,
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(fabricated)

    outcome = await runner.run(_request(), emit=lambda _event: None)

    assert outcome.report == prepared_analysis.fixture_outcome.report
    assert outcome.report.headline == "검색 실패 후 문의로 이어진 고객 6명"
    assert outcome.report.metrics[0].value == 6
    assert outcome.report.findings
    assert outcome.report.recommendations
    assert outcome.report.ranked_customers
    assert outcome.report.representative_journeys


async def test_positive_run_requires_journey_and_evidence_tool_provenance(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    incomplete = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=_empty_fabricated_draft(prepared_analysis.report),
        calls=prepared_analysis.calls[:4],
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(incomplete)

    with pytest.raises(GeminiRunnerError) as caught:
        await runner.run(_request(), emit=lambda _event: None)

    assert caught.value.code == "gemini_tool_policy_failed"


async def test_zero_match_voc_off_run_completes_from_four_tools(
    prepared_analysis: _PreparedAnalysis,
    repository: DuckDBRepository,
) -> None:
    request = _request(enabled_sources=["search_history", "search_feedback"])
    service = AnalyticsService(repository)
    scope = {
        "start_at": request.start_at.isoformat(),
        "end_at": request.end_at.isoformat(),
        "enabled_sources": list(request.enabled_sources),
    }
    catalog = service.catalog_sources(request.start_at, request.end_at)
    aggregate = service.aggregate_events(
        request.start_at,
        request.end_at,
        request.enabled_sources,
        group_by="topic",
    )
    matched = service.match_journey_pattern(
        request.start_at,
        request.end_at,
        request.enabled_sources,
    )
    ranked = service.rank_customers(
        request.start_at,
        request.end_at,
        request.enabled_sources,
    )
    assert matched.customer_count == 0
    results = [
        ("catalog_sources", {"start_at": scope["start_at"], "end_at": scope["end_at"]}, catalog),
        ("aggregate_events", {**scope, "group_by": "topic"}, aggregate),
        ("match_journey_pattern", scope, matched),
        ("rank_customers", scope, ranked),
    ]
    calls = [
        (
            name,
            arguments,
            CallToolResult(content=[], structuredContent=result.model_dump(mode="json")),
        )
        for name, arguments, result in results
    ]
    draft = _empty_fabricated_draft(prepared_analysis.report)
    draft.scope.enabled_sources = list(request.enabled_sources)
    zero = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=draft,
        calls=calls,
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(zero)

    outcome = await runner.run(request, emit=lambda _event: None)

    assert list(outcome.facts.tool_result_ids) == [
        "catalog_sources",
        "aggregate_events",
        "match_journey_pattern",
        "rank_customers",
    ]
    assert outcome.report.headline == "검색 실패 후 문의로 이어진 고객 0명"
    assert outcome.report.metrics[0].value == 0
    assert outcome.report.ranked_customers == []
    assert outcome.report.representative_journeys == []
    assert outcome.report.findings == []
    assert outcome.report.recommendations == []


async def test_evidence_must_match_the_captured_journey_event_provenance(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    name, arguments, response = prepared_analysis.calls[-1]
    assert response.structuredContent is not None
    tampered_content = json.loads(json.dumps(response.structuredContent))
    original_source = tampered_content["records"][0]["source_id"]
    tampered_content["records"][0]["source_id"] = (
        "voc" if original_source != "voc" else "search_history"
    )
    tampered = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=prepared_analysis.report,
        calls=[
            *prepared_analysis.calls[:-1],
            (
                name,
                arguments,
                CallToolResult(content=[], structuredContent=tampered_content),
            ),
        ],
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(tampered)
    events: list[RunnerEvent] = []

    with pytest.raises(GeminiRunnerError) as caught:
        await runner.run(_request(), emit=events.append)

    assert caught.value.code == "gemini_tool_execution_failed"
    assert "result" not in [event.type for event in events]


async def test_journey_result_customer_must_match_the_captured_tool_request(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    name, arguments, response = prepared_analysis.calls[-2]
    assert response.structuredContent is not None
    tampered_content = json.loads(json.dumps(response.structuredContent))
    tampered_content["customer_id"] = "CUST-FABRICATED"
    tampered = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=prepared_analysis.report,
        calls=[
            *prepared_analysis.calls[:-2],
            (
                name,
                arguments,
                CallToolResult(content=[], structuredContent=tampered_content),
            ),
            prepared_analysis.calls[-1],
        ],
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(tampered)
    events: list[RunnerEvent] = []

    with pytest.raises(GeminiRunnerError) as caught:
        await runner.run(_request(), emit=events.append)

    assert caught.value.code == "gemini_tool_execution_failed"
    assert "result" not in [event.type for event in events]


async def test_structured_report_scope_must_exactly_match_the_run_request(
    prepared_analysis: _PreparedAnalysis,
) -> None:
    tampered_report = prepared_analysis.report.model_copy(deep=True)
    tampered_report.scope.enabled_sources = ["voc"]
    tampered = _PreparedAnalysis(
        fixture_outcome=prepared_analysis.fixture_outcome,
        report=tampered_report,
        calls=prepared_analysis.calls,
    )
    runner, _mcp_factory, _model_factory, _agent_factory = _runner(tampered)
    events: list[RunnerEvent] = []

    with pytest.raises(GeminiRunnerError) as caught:
        await runner.run(_request(), emit=events.append)

    assert caught.value.code == "gemini_validation_failed"
    assert "result" not in [event.type for event in events]


class _StaticOutcomeRunner:
    def __init__(self, outcome: RunnerOutcome) -> None:
        self._outcome = outcome
        self.calls = 0

    async def run(self, request: RunRequest, *, emit: Any) -> RunnerOutcome:
        self.calls += 1
        await emit(RunnerEvent(type="plan", payload={"steps": ["fixture fallback"]}))
        await emit(
            RunnerEvent(
                type="result",
                payload={
                    "agent_mode": self._outcome.agent_mode,
                    "report": self._outcome.report.model_dump(mode="json"),
                },
            )
        )
        return self._outcome


class _FailingGeminiRunner:
    def __init__(self, code: str = "gemini_provider_failed") -> None:
        self.code = code
        self.calls = 0

    async def run(self, request: RunRequest, *, emit: Any) -> RunnerOutcome:
        self.calls += 1
        await emit(
            RunnerEvent(
                type="error",
                payload={
                    "code": self.code,
                    "message": "private provider response must be discarded",
                },
            )
        )
        raise GeminiRunnerError(self.code, "private provider response must be discarded")


class _BlockingGeminiRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, request: RunRequest, *, emit: Any) -> RunnerOutcome:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_auto_mode_falls_back_to_fixture_without_publishing_a_gemini_error(
    prepared_analysis: _PreparedAnalysis,
    repository: DuckDBRepository,
) -> None:
    fixture_runner = _StaticOutcomeRunner(prepared_analysis.fixture_outcome)
    gemini_runner = _FailingGeminiRunner()
    store = RunStore()
    coordinator = RunCoordinator(
        agent_mode="auto",
        fixture_runner=fixture_runner,
        gemini_runner=gemini_runner,
        analytics=AnalyticsService(repository),
        store=store,
    )

    created = coordinator.create_run(_request())
    terminal = await coordinator.wait_for_run(created.run_id)
    events = [event async for event in store.stream_events(created.run_id)]

    assert terminal.status == "completed"
    assert terminal.agent_mode == "fixture"
    assert gemini_runner.calls == fixture_runner.calls == 1
    assert [event.type for event in events] == ["fallback", "plan", "result", "done"]
    assert events[0].payload == {
        "from": "gemini",
        "to": "fixture",
        "code": "gemini_provider_failed",
        "message": "Gemini 분석을 사용할 수 없어 fixture 모드로 전환했습니다.",
    }
    serialized = json.dumps(
        [event.model_dump(mode="json") for event in events],
        ensure_ascii=False,
    ).lower()
    assert "private provider" not in serialized
    assert "error" not in [event.type for event in events]


async def test_auto_fallback_discards_a_result_from_the_abandoned_gemini_attempt(
    prepared_analysis: _PreparedAnalysis,
    repository: DuckDBRepository,
) -> None:
    class ResultThenFailGemini:
        async def run(self, request: RunRequest, *, emit: Any) -> RunnerOutcome:
            await emit(
                RunnerEvent(
                    type="result",
                    payload={
                        "agent_mode": "gemini",
                        "report": prepared_analysis.report.model_dump(mode="json"),
                    },
                )
            )
            raise GeminiRunnerError(
                "gemini_provider_failed",
                "private provider response must be discarded",
            )

    class SilentFixture:
        async def run(self, request: RunRequest, *, emit: Any) -> RunnerOutcome:
            await emit(RunnerEvent(type="plan", payload={"steps": ["fixture fallback"]}))
            return prepared_analysis.fixture_outcome

    store = RunStore()
    coordinator = RunCoordinator(
        agent_mode="auto",
        fixture_runner=SilentFixture(),
        gemini_runner=ResultThenFailGemini(),
        analytics=AnalyticsService(repository),
        store=store,
    )

    created = coordinator.create_run(_request())
    terminal = await coordinator.wait_for_run(created.run_id)
    events = [event async for event in store.stream_events(created.run_id)]

    assert terminal.status == "completed"
    assert terminal.agent_mode == "fixture"
    assert [event.type for event in events] == ["fallback", "plan", "done"]


async def test_forced_gemini_mode_fails_explicitly_without_running_fixture(
    prepared_analysis: _PreparedAnalysis,
    repository: DuckDBRepository,
) -> None:
    fixture_runner = _StaticOutcomeRunner(prepared_analysis.fixture_outcome)
    gemini_runner = _FailingGeminiRunner(code="gemini_not_configured")
    store = RunStore()
    coordinator = RunCoordinator(
        agent_mode="gemini",
        fixture_runner=fixture_runner,
        gemini_runner=gemini_runner,
        analytics=AnalyticsService(repository),
        store=store,
    )

    created = coordinator.create_run(_request())
    terminal = await coordinator.wait_for_run(created.run_id)
    events = [event async for event in store.stream_events(created.run_id)]

    assert terminal.status == "failed"
    assert terminal.error is not None
    assert terminal.error.code == "gemini_not_configured"
    assert "private provider" not in terminal.error.message.lower()
    assert fixture_runner.calls == 0
    assert [event.type for event in events] == ["error", "done"]
    assert (
        "private provider"
        not in json.dumps(
            [event.model_dump(mode="json") for event in events],
            ensure_ascii=False,
        ).lower()
    )


async def test_forced_gemini_timeout_fails_with_a_safe_typed_error(
    repository: DuckDBRepository,
) -> None:
    gemini_runner = _BlockingGeminiRunner()
    store = RunStore()
    coordinator = RunCoordinator(
        agent_mode="gemini",
        gemini_runner=gemini_runner,
        gemini_timeout_seconds=0.01,
        analytics=AnalyticsService(repository),
        store=store,
    )
    created = coordinator.create_run(_request())

    try:
        terminal = await asyncio.wait_for(coordinator.wait_for_run(created.run_id), timeout=0.5)
    finally:
        await coordinator.close()
    events = [event async for event in store.stream_events(created.run_id)]

    assert terminal.status == "failed"
    assert terminal.error is not None
    assert terminal.error.code == "gemini_timeout"
    assert terminal.error.message == "Gemini 분석 시간이 초과됐습니다."
    assert [event.type for event in events] == ["error", "done"]


async def test_auto_gemini_timeout_emits_explicit_timeout_fallback(
    prepared_analysis: _PreparedAnalysis,
    repository: DuckDBRepository,
) -> None:
    fixture_runner = _StaticOutcomeRunner(prepared_analysis.fixture_outcome)
    gemini_runner = _BlockingGeminiRunner()
    store = RunStore()
    coordinator = RunCoordinator(
        agent_mode="auto",
        fixture_runner=fixture_runner,
        gemini_runner=gemini_runner,
        gemini_timeout_seconds=0.01,
        analytics=AnalyticsService(repository),
        store=store,
    )
    created = coordinator.create_run(_request())

    terminal = await asyncio.wait_for(coordinator.wait_for_run(created.run_id), timeout=0.5)
    events = [event async for event in store.stream_events(created.run_id)]

    assert terminal.status == "completed"
    assert terminal.agent_mode == "fixture"
    assert events[0].type == "fallback"
    assert events[0].payload == {
        "from": "gemini",
        "to": "fixture",
        "code": "gemini_timeout",
        "message": "Gemini 분석 시간이 초과되어 fixture 모드로 전환했습니다.",
    }
    assert "error" not in [event.type for event in events]


async def test_auto_mode_preserves_cancellation_without_fallback(
    prepared_analysis: _PreparedAnalysis,
    repository: DuckDBRepository,
) -> None:
    fixture_runner = _StaticOutcomeRunner(prepared_analysis.fixture_outcome)

    gemini_runner = _BlockingGeminiRunner()
    store = RunStore()
    coordinator = RunCoordinator(
        agent_mode="auto",
        fixture_runner=fixture_runner,
        gemini_runner=gemini_runner,
        analytics=AnalyticsService(repository),
        store=store,
    )
    created = coordinator.create_run(_request())
    await asyncio.wait_for(gemini_runner.started.wait(), timeout=1)

    await coordinator.close()

    terminal = store.get_snapshot(created.run_id)
    events = [event async for event in store.stream_events(created.run_id)]
    assert terminal.status == "failed"
    assert terminal.error is not None
    assert terminal.error.code == "run_cancelled"
    assert fixture_runner.calls == 0
    assert [event.type for event in events] == ["error", "done"]


def _wait_for_api_terminal(client: TestClient, status_url: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(status_url).json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    pytest.fail("run did not reach a terminal state")


def _api_request() -> dict[str, Any]:
    return _request().model_dump(mode="json")


def _sse_event_types(body: str) -> list[str]:
    return [
        line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
    ]


def test_api_auto_mode_without_a_key_emits_fallback_then_runs_fixture(tmp_path) -> None:
    settings = Settings(
        agent_mode="auto",
        gemini_api_key="",
        database_path=tmp_path / "auto.duckdb",
        _env_file=None,
    )

    with TestClient(create_app(settings)) as client:
        accepted = client.post("/api/runs", json=_api_request()).json()
        terminal = _wait_for_api_terminal(client, accepted["status_url"])
        events = client.get(accepted["events_url"])

    assert terminal["status"] == "completed"
    assert terminal["agent_mode"] == "fixture"
    assert _sse_event_types(events.text)[0] == "fallback"
    assert "error" not in _sse_event_types(events.text)


def test_api_forced_gemini_without_a_key_fails_without_running_fixture(tmp_path) -> None:
    settings = Settings(
        agent_mode="gemini",
        gemini_api_key="",
        database_path=tmp_path / "forced.duckdb",
        _env_file=None,
    )

    with TestClient(create_app(settings)) as client:
        accepted = client.post("/api/runs", json=_api_request()).json()
        terminal = _wait_for_api_terminal(client, accepted["status_url"])
        events = client.get(accepted["events_url"])

    assert terminal["status"] == "failed"
    assert terminal["agent_mode"] is None
    assert terminal["error"]["code"] == "gemini_not_configured"
    assert _sse_event_types(events.text) == ["error", "done"]
