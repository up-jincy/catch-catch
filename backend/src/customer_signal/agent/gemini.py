"""Lazy Gemini deep-agent adapter with run-local MCP provenance capture."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from time import perf_counter
from typing import Any, Protocol, cast

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain.agents.middleware import TodoListMiddleware
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from mcp.types import CallToolResult
from pydantic import ValidationError

from customer_signal.agent.contracts import (
    EventEmitter,
    ReportValidator,
    RunRequest,
    RunnerOutcome,
    ToolName,
    UnsupportedClaimError,
)
from customer_signal.agent.facts import build_run_facts
from customer_signal.agent.report_composer import compose_verified_report
from customer_signal.agent.validator import validate_report
from customer_signal.analytics.models import (
    AggregateResult,
    AnalyticsResultModel,
    CatalogSourcesResult,
    CustomerJourneyResult,
    EvidenceResult,
    PatternMatchResult,
    RankCustomersResult,
)
from customer_signal.domain.models import SourceId
from customer_signal.domain.reports import InsightReport
from customer_signal.runtime.events import RunnerEvent


_PLAN_STEPS = [
    "요청 범위와 분석 계획 확인",
    "MCP 분석 Tool로 근거 수집",
    "구조화 보고서와 Run 근거 검증",
]
_TOOL_RESULT_TYPES: dict[ToolName, type[AnalyticsResultModel]] = {
    "catalog_sources": CatalogSourcesResult,
    "aggregate_events": AggregateResult,
    "match_journey_pattern": PatternMatchResult,
    "rank_customers": RankCustomersResult,
    "get_customer_journey": CustomerJourneyResult,
    "get_evidence": EvidenceResult,
}
_REQUIRED_TOOLS = frozenset(
    {
        "catalog_sources",
        "aggregate_events",
        "match_journey_pattern",
        "rank_customers",
    }
)
_BOUNDED_TOOLS = frozenset(
    {
        "aggregate_events",
        "match_journey_pattern",
        "rank_customers",
        "get_customer_journey",
    }
)
_DEEP_AGENT_EXCLUDED_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    }
)
_PROFILE_LOCK = Lock()
_PROFILE_REGISTERED = False
_SYSTEM_PROMPT = """You are a bounded customer-signal analytics agent.
Use only the supplied read-only customer_signal MCP tools for data claims.
Call each MCP tool at most once and make no more than six MCP calls total.
Use the exact request time range and enabled source allowlist.
Always call catalog_sources, aggregate_events(group_by='topic'),
match_journey_pattern, and rank_customers. When match_journey_pattern returns a
positive customer_count, you MUST call get_customer_journey exactly once for the
first matched customer, then MUST call get_evidence exactly once for evidence IDs
shared by that Journey and the representative evidence allowlist. When
customer_count is zero, omit both detail calls.
Omit bounded-tool limits or set them to exactly the integer 100.
Never invent customer IDs, evidence IDs, result IDs, counts, scores, or sources.
Return only the InsightReport structured response. Do not expose reasoning or tool raw data.
"""


class GeminiRunnerError(RuntimeError):
    """Safe, typed adapter failure suitable for coordinator policy decisions."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Agent(Protocol):
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class _RunCapture:
    request: RunRequest
    emit: EventEmitter
    tool_names: list[ToolName] = field(default_factory=list)
    results: dict[ToolName, AnalyticsResultModel] = field(default_factory=dict)


_ACTIVE_CAPTURE: ContextVar[_RunCapture | None] = ContextVar(
    "customer_signal_gemini_capture",
    default=None,
)


async def _emit(emit: EventEmitter, event: RunnerEvent) -> None:
    pending = emit(event)
    if inspect.isawaitable(pending):
        await pending


def _same_datetime(value: object, expected: datetime) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False
    else:
        return False
    return parsed == expected


def _is_model_not_found(error: Exception) -> bool:
    code = getattr(error, "code", None)
    status = getattr(error, "status", None)
    if code == 404:
        return True
    return isinstance(status, str) and status.upper() in {"404", "NOT_FOUND"}


def _public_failure(error: Exception) -> GeminiRunnerError:
    if isinstance(error, GeminiRunnerError):
        return error
    if _is_model_not_found(error):
        return GeminiRunnerError(
            "gemini_model_not_found",
            "사용 가능한 Gemini 분석 모델을 찾지 못했습니다.",
        )
    if isinstance(error, (UnsupportedClaimError, ValidationError, ValueError, KeyError)):
        return GeminiRunnerError(
            "gemini_validation_failed",
            "Gemini 분석 결과 검증에 실패했습니다.",
        )
    return GeminiRunnerError(
        "gemini_provider_failed",
        "Gemini 분석 서비스 호출에 실패했습니다.",
    )


def _ensure_bounded_google_genai_profile() -> None:
    global _PROFILE_REGISTERED
    if _PROFILE_REGISTERED:
        return
    with _PROFILE_LOCK:
        if _PROFILE_REGISTERED:
            return
        register_harness_profile(
            "google_genai",
            HarnessProfile(
                excluded_tools=_DEEP_AGENT_EXCLUDED_TOOLS,
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )
        _PROFILE_REGISTERED = True


class GeminiRunner:
    """Run a structured deep agent without exposing provider state or transcripts."""

    def __init__(
        self,
        *,
        api_key: str | None,
        mcp_url: str,
        primary_model: str = "gemini-3.7-flash",
        fallback_model: str = "gemini-3.6-flash",
        validator: ReportValidator = validate_report,
        mcp_client_factory: Callable[..., Any] = MultiServerMCPClient,
        model_factory: Callable[..., Any] = ChatGoogleGenerativeAI,
        agent_factory: Callable[..., Any] = create_deep_agent,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._mcp_url = mcp_url
        self._primary_model = primary_model
        self._fallback_model = fallback_model
        self._validator = validator
        self._mcp_client_factory = mcp_client_factory
        self._model_factory = model_factory
        self._agent_factory = agent_factory
        self._init_lock = asyncio.Lock()
        self._mcp_client: Any | None = None
        self._tools: Sequence[Any] | None = None
        self._agents: dict[str, _Agent] = {}

    @property
    def is_configured(self) -> bool:
        """Whether a nonblank provider key was supplied, without exposing it."""

        return self._api_key is not None

    async def _get_agent(self, model_name: str) -> _Agent:
        if self._api_key is None:
            raise GeminiRunnerError(
                "gemini_not_configured",
                "Gemini API Key가 설정되지 않았습니다.",
            )
        async with self._init_lock:
            existing = self._agents.get(model_name)
            if existing is not None:
                return existing
            if self._tools is None:
                self._mcp_client = self._mcp_client_factory(
                    {
                        "customer_signal": {
                            "transport": "streamable_http",
                            "url": self._mcp_url,
                        }
                    },
                    tool_interceptors=[self._capture_tool_call],
                )
                self._tools = await self._mcp_client.get_tools()
            model = self._model_factory(
                model=model_name,
                api_key=self._api_key,
                # Analytics runs favor repeatable structured output over creative variance.
                temperature=0,
                retries=0,
                request_timeout=40,
                include_thoughts=False,
            )
            _ensure_bounded_google_genai_profile()
            agent = cast(
                _Agent,
                self._agent_factory(
                    model=model,
                    tools=self._tools,
                    middleware=[TodoListMiddleware()],
                    response_format=InsightReport,
                    system_prompt=_SYSTEM_PROMPT,
                ),
            )
            self._agents[model_name] = agent
            return agent

    def _validate_scope(self, capture: _RunCapture, request: MCPToolCallRequest) -> None:
        if request.name in _BOUNDED_TOOLS and "limit" in request.args:
            limit = request.args["limit"]
            if type(limit) is not int or limit != 100:
                raise GeminiRunnerError(
                    "gemini_tool_policy_failed",
                    "Gemini bounded Tool limit은 100이어야 합니다.",
                )
        if request.name == "get_evidence":
            evidence_ids = request.args.get("evidence_ids")
            journey = capture.results.get("get_customer_journey")
            allowed = (
                set(journey.evidence_ids) if isinstance(journey, CustomerJourneyResult) else set()
            )
            if (
                not isinstance(evidence_ids, list)
                or not evidence_ids
                or not all(isinstance(item, str) for item in evidence_ids)
                or len(evidence_ids) != len(set(evidence_ids))
                or not set(evidence_ids) <= allowed
            ):
                raise GeminiRunnerError(
                    "gemini_tool_policy_failed",
                    "Gemini Tool 호출 범위가 Run 요청과 일치하지 않습니다.",
                )
            return

        if not _same_datetime(request.args.get("start_at"), capture.request.start_at):
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "Gemini Tool 호출 범위가 Run 요청과 일치하지 않습니다.",
            )
        if not _same_datetime(request.args.get("end_at"), capture.request.end_at):
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "Gemini Tool 호출 범위가 Run 요청과 일치하지 않습니다.",
            )
        if request.name != "catalog_sources":
            if request.args.get("enabled_sources") != list(capture.request.enabled_sources):
                raise GeminiRunnerError(
                    "gemini_tool_policy_failed",
                    "Gemini Tool 호출 범위가 Run 요청과 일치하지 않습니다.",
                )
        if request.name == "aggregate_events" and request.args.get("group_by", "source") != "topic":
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "Gemini 집계 Tool은 Topic 기준으로 호출해야 합니다.",
            )
        if request.name == "get_customer_journey":
            allowed_customers: set[str] = set()
            matched = capture.results.get("match_journey_pattern")
            ranked = capture.results.get("rank_customers")
            if isinstance(matched, PatternMatchResult):
                allowed_customers.update(matched.customer_ids)
            if isinstance(ranked, RankCustomersResult):
                allowed_customers.update(customer.customer_id for customer in ranked.customers)
            if request.args.get("customer_id") not in allowed_customers:
                raise GeminiRunnerError(
                    "gemini_tool_policy_failed",
                    "Gemini Journey Tool 고객이 Run 결과에 포함되지 않았습니다.",
                )

    async def _capture_tool_call(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        capture = _ACTIVE_CAPTURE.get()
        if capture is None:
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "활성 Gemini Run 없이 Tool을 호출할 수 없습니다.",
            )
        if request.name not in _TOOL_RESULT_TYPES:
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "허용되지 않은 Gemini Tool 호출입니다.",
            )
        tool_name = cast(ToolName, request.name)
        if tool_name in capture.tool_names or len(capture.tool_names) >= 6:
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "Gemini Tool은 Run당 중복 없이 최대 6회 호출할 수 있습니다.",
            )
        self._validate_scope(capture, request)
        capture.tool_names.append(tool_name)
        source: list[SourceId] = (
            [] if tool_name == "catalog_sources" else list(capture.request.enabled_sources)
        )
        await _emit(
            capture.emit,
            RunnerEvent(
                type="tool_started",
                payload={"tool": tool_name, "source": source},
            ),
        )
        started_at = perf_counter()
        response = await handler(request)
        if (
            not isinstance(response, CallToolResult)
            or response.isError
            or not isinstance(response.structuredContent, dict)
        ):
            raise GeminiRunnerError(
                "gemini_tool_execution_failed",
                "Gemini MCP Tool 실행에 실패했습니다.",
            )
        result_type = _TOOL_RESULT_TYPES[tool_name]
        try:
            result = result_type.model_validate_json(
                json.dumps(response.structuredContent, ensure_ascii=False)
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise GeminiRunnerError(
                "gemini_tool_execution_failed",
                "Gemini MCP Tool 결과 검증에 실패했습니다.",
            ) from error
        if tool_name == "get_customer_journey":
            journey_result = cast(CustomerJourneyResult, result)
            if journey_result.customer_id != request.args.get("customer_id"):
                raise GeminiRunnerError(
                    "gemini_tool_execution_failed",
                    "Gemini MCP Journey 결과가 요청 고객과 일치하지 않습니다.",
                )
        if tool_name == "aggregate_events":
            aggregate_result = cast(AggregateResult, result)
            if aggregate_result.group_by != request.args.get("group_by", "source"):
                raise GeminiRunnerError(
                    "gemini_tool_execution_failed",
                    "Gemini MCP 집계 결과가 요청 기준과 일치하지 않습니다.",
                )
        if tool_name == "get_evidence":
            evidence = cast(EvidenceResult, result)
            requested_ids = cast(list[str], request.args["evidence_ids"])
            journey = cast(
                CustomerJourneyResult,
                capture.results["get_customer_journey"],
            )
            if (
                evidence.evidence_ids != requested_ids
                or [record.evidence_id for record in evidence.records] != requested_ids
            ):
                raise GeminiRunnerError(
                    "gemini_tool_execution_failed",
                    "Gemini MCP Evidence 결과가 요청과 일치하지 않습니다.",
                )
            journey_events = {event.evidence_id: event for event in journey.events}
            if any(
                (event := journey_events.get(record.evidence_id)) is None
                or event.source_id != record.source_id
                or event.occurred_at != record.occurred_at
                for record in evidence.records
            ):
                raise GeminiRunnerError(
                    "gemini_tool_execution_failed",
                    "Gemini MCP Evidence 출처가 Journey와 일치하지 않습니다.",
                )
            source = list(dict.fromkeys(record.source_id for record in evidence.records))
        capture.results[tool_name] = result
        await _emit(
            capture.emit,
            RunnerEvent(
                type="tool_completed",
                payload={
                    "tool": tool_name,
                    "source": source,
                    "count": result.stats.returned_rows,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    "result_id": result.result_id,
                },
            ),
        )
        return response

    async def _invoke(self, model_name: str, state: dict[str, Any]) -> dict[str, Any]:
        agent = await self._get_agent(model_name)
        result = await agent.ainvoke(state)
        if not isinstance(result, dict):
            raise GeminiRunnerError(
                "gemini_validation_failed",
                "Gemini 분석 결과 검증에 실패했습니다.",
            )
        return result

    def _build_outcome(self, capture: _RunCapture, state: dict[str, Any]) -> RunnerOutcome:
        if not _REQUIRED_TOOLS <= set(capture.results):
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "Gemini 분석에 필요한 MCP Tool 결과가 없습니다.",
            )
        optional_tools = set(capture.results) - _REQUIRED_TOOLS
        if optional_tools not in (set(), {"get_customer_journey", "get_evidence"}):
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "Gemini Journey와 Evidence Tool은 함께 호출해야 합니다.",
            )
        matched = cast(PatternMatchResult, capture.results["match_journey_pattern"])
        has_positive_matches = matched.customer_count > 0
        if has_positive_matches and optional_tools != {"get_customer_journey", "get_evidence"}:
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "양수 Gemini 결과에는 Journey와 Evidence Tool이 필요합니다.",
            )
        if not has_positive_matches and optional_tools:
            raise GeminiRunnerError(
                "gemini_tool_policy_failed",
                "0명 Gemini 결과는 네 개의 집계 Tool로 완료해야 합니다.",
            )
        structured = state.get("structured_response")
        try:
            if isinstance(structured, InsightReport):
                draft = structured
            else:
                draft = InsightReport.model_validate_json(
                    json.dumps(structured, ensure_ascii=False)
                )
            if (
                draft.scope.start_at != capture.request.start_at
                or draft.scope.end_at != capture.request.end_at
                or draft.scope.enabled_sources != list(capture.request.enabled_sources)
            ):
                raise UnsupportedClaimError("Gemini 보고서 Scope가 Run 요청과 일치하지 않습니다.")
            journey = cast(
                CustomerJourneyResult | None,
                capture.results.get("get_customer_journey"),
            )
            evidence = cast(EvidenceResult | None, capture.results.get("get_evidence"))
            catalog = cast(CatalogSourcesResult, capture.results["catalog_sources"])
            aggregate = cast(AggregateResult, capture.results["aggregate_events"])
            ranked = cast(RankCustomersResult, capture.results["rank_customers"])
            report = compose_verified_report(
                capture.request,
                catalog=catalog,
                aggregate=aggregate,
                matched=matched,
                ranked=ranked,
                journey=journey,
                evidence=evidence,
            )
            facts = build_run_facts(
                capture.request,
                catalog=catalog,
                aggregate=aggregate,
                matched=matched,
                ranked=ranked,
                journey=journey,
                evidence=evidence,
                representative_customer_id=(journey.customer_id if journey is not None else None),
            )
            self._validator(report, facts)
        except (UnsupportedClaimError, ValidationError, TypeError, ValueError, KeyError) as error:
            raise GeminiRunnerError(
                "gemini_validation_failed",
                "Gemini 분석 결과 검증에 실패했습니다.",
            ) from error
        return RunnerOutcome(report=report, facts=facts, agent_mode="gemini")

    async def run(
        self,
        request: RunRequest,
        *,
        emit: EventEmitter,
    ) -> RunnerOutcome:
        if self._api_key is None:
            raise GeminiRunnerError(
                "gemini_not_configured",
                "Gemini API Key가 설정되지 않았습니다.",
            )
        await _emit(emit, RunnerEvent(type="plan", payload={"steps": _PLAN_STEPS}))
        capture = _RunCapture(request=request.model_copy(deep=True), emit=emit)
        token = _ACTIVE_CAPTURE.set(capture)
        state = {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": request.question,
                            "start_at": request.start_at.isoformat(),
                            "end_at": request.end_at.isoformat(),
                            "enabled_sources": request.enabled_sources,
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        }
        try:
            try:
                result_state = await self._invoke(self._primary_model, state)
            except Exception as error:
                if (
                    _is_model_not_found(error)
                    and not capture.tool_names
                    and self._fallback_model != self._primary_model
                ):
                    result_state = await self._invoke(self._fallback_model, state)
                else:
                    raise _public_failure(error) from error
            outcome = self._build_outcome(capture, result_state)
            await _emit(
                emit,
                RunnerEvent(
                    type="validating",
                    payload={"result_ids": list(outcome.facts.tool_result_ids.values())},
                ),
            )
            await _emit(
                emit,
                RunnerEvent(
                    type="result",
                    payload={
                        "agent_mode": outcome.agent_mode,
                        "report": outcome.report.model_dump(mode="json"),
                    },
                ),
            )
            return outcome
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _public_failure(error) from error
        finally:
            _ACTIVE_CAPTURE.reset(token)


__all__ = ["GeminiRunner", "GeminiRunnerError"]
