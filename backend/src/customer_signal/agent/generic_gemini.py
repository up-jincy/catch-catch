"""Staged Gemini model adapter for the validated generic Analysis Loop."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from customer_signal.agent.contracts import (
    ReportModelContext,
    RunRequest,
    SelectionContext,
    StepModelContext,
)
from customer_signal.agent.generic_fixture import GenericFixtureModel
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    CustomerSignalReportDraft,
    GoalDecision,
    StepSelection,
    UnsupportedAnalysis,
)
from customer_signal.domain.primitives import PRIMITIVE_INPUT_ADAPTER
from customer_signal.domain.sources import PublicSourceManifest, SourceManifest
from customer_signal.observability.langfuse import build_langfuse_config


T = TypeVar("T")


class _JsonDocument(BaseModel):
    """Flat provider schema; detailed contracts stay server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    document: str = Field(min_length=2, max_length=120_000)


class GeminiAnalysisError(RuntimeError):
    """Safe, typed Gemini failure suitable for the generic Run error boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GeminiAnalysisModel:
    """Request one strict draft per stage; never publish provider prose directly."""

    agent_mode = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None,
        primary_model: str = "gemini-3.7-flash",
        fallback_model: str = "gemini-3.6-flash",
        timeout_seconds: float = 40.0,
        model_factory: Callable[..., Any] = ChatGoogleGenerativeAI,
    ) -> None:
        if not primary_model.strip() or not fallback_model.strip():
            raise ValueError("Gemini model names must be nonblank")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 40
        ):
            raise ValueError("Gemini call timeout must be finite and between 0 and 40 seconds")
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._primary_model = primary_model.strip()
        self._fallback_model = fallback_model.strip()
        self._selected_model = self._primary_model
        self._timeout_seconds = float(timeout_seconds)
        self._model_factory = model_factory
        self._models: dict[str, Any] = {}

    @property
    def is_configured(self) -> bool:
        return self._api_key is not None

    @property
    def model_name(self) -> str:
        """Actual model selected for the current and subsequent staged calls."""

        return self._selected_model

    async def create_goal(
        self,
        request: RunRequest,
        manifests: list[SourceManifest],
    ) -> GoalDecision:
        guard_decision = await GenericFixtureModel().create_goal(request, manifests)
        if (
            isinstance(guard_decision, UnsupportedAnalysis)
            and guard_decision.code == "pii_request"
        ):
            return guard_decision
        return await self._invoke_document(
            output_type=GoalDecision,
            schema_title="GoalDecision",
            stage="goal",
            public_input={
                "request": request.model_dump(mode="json"),
                "sources": _public_manifests(manifests),
                "primitive_catalog": _primitive_catalog(),
                "decision_policy": {
                    "choose_exactly_one": ["goal", "clarification", "unsupported"],
                    "clarification_when": (
                        "질문이 측정할 신호 자체를 담고 있지 않을 때만 clarification을 "
                        "반환한다 (예: '분석해줘', '문제 있는 고객 알려줘' — 무엇이 "
                        "'문제'인지, 어떤 신호인지 없음). 이때 분석을 시작할 수 있게 "
                        "하는 구체적 질문 하나를 던진다"
                    ),
                    "no_clarification_when": (
                        "질문이 신호(예: 부정 피드백, 반복 행동 뒤 상담 전환, 가입 "
                        "미완료)와 원하는 산출물(주제, 고객 수, 여정, 이탈 단계)을 "
                        "담고 있으면 절대 되묻지 않는다 — 집계 방식이나 분류 기준 "
                        "같은 세부 선택은 질문에 가장 직접적인 해석 하나를 스스로 "
                        "골라 goal로 만든다"
                    ),
                    "goal_when": (
                        "질문이 측정 가능한 결과를 특정하면 goal을 반환한다. "
                        "objective는 제공된 sources와 primitives로 계산 가능해야 하고, "
                        "time_range는 요청의 start_at/end_at을 그대로 사용하며, "
                        "source_ids에는 질문이 의존하는 이벤트를 가진 enabled source를 "
                        "모두 포함한다 (질문이 특정 source로 좁히지 않는 한 임의로 "
                        "좁히지 않는다). 행동 순서 질문이면 질문이 명시한 조건 — 같은 "
                        "행동의 반복 여부, 시간 창, 전환 대상 이벤트 — 을 sequence에 "
                        "빠짐없이 반영한다; 조건을 느슨하게 풀면 잘못된 고객이 "
                        "매칭된다"
                    ),
                    "unsupported_when": (
                        "PII·raw export·쓰기 요청이거나 어떤 primitive로도 측정할 수 "
                        "없는 경우에만 unsupported를 반환한다"
                    ),
                    "goal_archetypes": [
                        {
                            "question_shape": "특정 신호가 집중된 주제/속성과 그 고객 규모",
                            "goal": (
                                "해당 신호 이벤트를 주제별로 집계해 고객 수를 세는 "
                                "objective; measures는 고객 수 집계; 신호를 가진 모든 "
                                "관련 source를 source_ids에 포함"
                            ),
                        },
                        {
                            "question_shape": "행동 A 뒤 행동 B로 이어진 고객 (전환/여정)",
                            "goal": (
                                "sequence에 A→B 순서와 질문의 시간·반복 조건을 그대로 "
                                "담은 objective; A와 B 이벤트를 가진 source를 모두 포함"
                            ),
                        },
                        {
                            "question_shape": "시작했지만 완료하지 못한 고객과 이탈 지점",
                            "goal": (
                                "시작 이벤트 후 완료 이벤트 부재를 sequence로 정의하고 "
                                "이탈 단계 구분을 group_by/segment로 담은 objective"
                            ),
                        },
                    ],
                },
            },
            allow_initial_fallback=True,
        )

    async def create_plan(
        self,
        goal: AnalysisGoal,
        manifests: list[SourceManifest],
        *,
        validation_feedback: str | None = None,
    ) -> AnalysisPlan:
        return await self._invoke_document(
            output_type=AnalysisPlan,
            schema_title="AnalysisPlan",
            stage="plan",
            public_input={
                "goal": goal.model_dump(mode="json"),
                "sources": _public_manifests(manifests),
                "primitive_catalog": _primitive_catalog(),
                "validation_feedback": validation_feedback,
                "planning_policy": {
                    "minimal_direct_plan": (
                        "목표를 직접 측정하는 최소 단계만 계획한다. 마지막 분석 "
                        "단계의 required metric이 사용자 질문에 답하는 숫자여야 한다"
                    ),
                    "primitive_selection_guide": {
                        "aggregate_events": (
                            "특정 이벤트·피드백 속성을 가진 고객 수/이벤트 수 집계 "
                            "(예: 부정 피드백이 많은 Topic별 고객 수)"
                        ),
                        "match_sequence": (
                            "순서가 있는 행동 패턴 매칭 (A 후 B, 시간 제한 포함 — "
                            "예: 반복 행동 뒤 상담 전환, 가입 시작 후 미완료)"
                        ),
                        "segment_customers": "모집단을 명명된 cohort로 분할",
                        "detect_repetition": "동일 행동의 반복 탐지",
                        "compare_segments": "두 선행 단계 metric의 비교",
                    },
                    "source_scope": (
                        "goal.source_ids의 source를 그대로 사용한다 — 질문이 "
                        "명시적으로 좁히지 않는 한 source를 임의로 제외하지 않는다"
                    ),
                    "time_scope": "goal.time_range를 모든 단계에 그대로 적용한다",
                    "answer_metric_last": (
                        "profile_events·catalog_sources·get_evidence는 준비/근거 "
                        "단계일 뿐 질문의 답이 아니다. 질문이 '몇 명/어떤 주제/어느 "
                        "단계'를 물으면 마지막 분석 단계는 반드시 그 숫자를 내는 "
                        "primitive여야 한다: 주제·속성별 고객 수 → aggregate_events, "
                        "행동 순서 매칭 고객 수 → match_sequence, cohort 분할 → "
                        "segment_customers. '시작했지만 완료하지 못한' 유형은 "
                        "aggregate_events가 아니라 match_sequence(시작 후 완료 부재) "
                        "+ segment_customers(이탈 단계)로 계획한다"
                    ),
                    "qualifier_predicates": (
                        "질문의 한정어 — 부정적/실패한/반복된/완료하지 못한/특정 "
                        "topic·채널 — 는 반드시 해당 단계의 predicates 또는 sequence "
                        "조건으로 인코딩한다 (sources manifests의 "
                        "supported_outcomes/topics/dimensions 값을 사용). 한정어를 "
                        "빼고 전체 모집단을 세는 계획은 오답이다. '상세 집계는 후속 "
                        "단계에서'라는 계획을 만들지 말고 이 plan 안에서 질문에 "
                        "완전히 답한다"
                    ),
                },
                "constraints": {
                    "initial_revision": 0,
                    "dependency_arity": {
                        "catalog_sources": {"minimum": 0, "maximum": 0},
                        "profile_events": {"minimum": 0, "maximum": 0},
                        "aggregate_events": {"minimum": 0, "maximum": 0},
                        "segment_customers": {"minimum": 0, "maximum": 0},
                        "detect_repetition": {"minimum": 0, "maximum": 0},
                        "match_sequence": {"minimum": 0, "maximum": 0},
                        "compare_segments": {"minimum": 2, "maximum": 2},
                        "rank_customers": {"minimum": 1, "maximum": 4},
                        "get_customer_journey": {"minimum": 1, "maximum": 1},
                        "get_evidence": {"minimum": 1, "maximum": 1},
                    },
                    "step_count": "3..6",
                    "first_step_should_discover_sources": True,
                    "input_step_ids": (
                        "must obey dependency_arity bounds and reference prior steps only"
                    ),
                    "compare_segments": (
                        "two dependencies must both publish parameters.metric_key; "
                        "required output is <metric_key>_delta"
                    ),
                    "required_metric_keys": {
                        "catalog_sources": ["source_count"],
                        "profile_events": ["customer_count", "event_count"],
                        "aggregate_events": ["exactly one requested metric key"],
                        "segment_customers": ["segment_customer_count"],
                        "detect_repetition": ["repeated_customer_count"],
                        "match_sequence": ["matched_customer_count"],
                        "compare_segments": ["<parameters.metric_key>_delta"],
                        "rank_customers": ["ranked_customer_count"],
                        "get_customer_journey": ["journey_event_count"],
                        "get_evidence": ["evidence_record_count"],
                    },
                    "read_only": True,
                },
            },
        )

    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft:
        return await self._invoke_document(
            output_type=AnalysisNoteDraft,
            schema_title="AnalysisNoteDraft",
            stage="note",
            public_input={
                "context": context.model_dump(mode="json"),
                "claim_constraints": {
                    "cardinality": (
                        "each Claim must bind exactly one Fact through exactly one FactRef"
                    ),
                    "fact_ref_binding": {
                        "fact_id": context.current_fact.fact_id,
                        "result_id": context.current_fact.result_id,
                        "plan_revision": context.plan.revision,
                    },
                    "claim_type_rules": {
                        "metric": {
                            "subject": "selected Fact metric_key",
                            "operator": "eq",
                            "target": "selected Fact metric exact typed value",
                            "selector": "metric_key only",
                            "optional_selector_fields": ["label", "unit", "dimensions"],
                        },
                        "segment": {
                            "subject": "segment_id",
                            "operator": "eq",
                            "target": "selected FactRef segment_id exact string",
                            "selector": "segment_id only",
                        },
                        "customer": {
                            "subject": "customer_id",
                            "operator": "eq",
                            "target": "selected FactRef customer_id exact string",
                            "selector": "customer_id only",
                        },
                        "source": {
                            "subject": "source_id",
                            "operator": "eq",
                            "target": "selected FactRef source_id exact string",
                            "selector": "source_id only",
                        },
                        "evidence": {
                            "subject": "evidence_id",
                            "operator": "eq",
                            "target": "selected FactRef evidence_id exact string",
                            "selector": "evidence_id only",
                        },
                    },
                    "availability_rule": (
                        "do not create a Claim type or selector that is absent from current_fact"
                    ),
                },
            },
        )

    async def select_next(self, context: SelectionContext) -> StepSelection:
        return await self._invoke_document(
            output_type=StepSelection,
            schema_title="StepSelection",
            stage="selection",
            public_input={"context": context.model_dump(mode="json")},
        )

    async def create_report(
        self,
        context: ReportModelContext,
    ) -> CustomerSignalReportDraft:
        return await self._invoke_document(
            output_type=CustomerSignalReportDraft,
            schema_title="CustomerSignalReportDraft",
            stage="report",
            public_input={
                "context": context.model_dump(mode="json"),
                "report_constraints": {
                    "goal_id": {"required_exact": context.goal.goal_id},
                    "claim_refs": {
                        "allowed_values": [
                            claim.claim_id
                            for note in context.notes
                            for claim in note.claims
                        ],
                        "rule": "subset only",
                    },
                    "recommended_actions.fact_refs": {
                        "allowed_values": [fact.fact_id for fact in context.facts],
                        "rule": "subset only",
                    },
                    "recommended_actions.claim_refs": {
                        "rule": "subset of selected top-level claim_refs only",
                    },
                },
            },
        )

    async def _invoke_document(
        self,
        *,
        output_type: Any,
        schema_title: str,
        stage: str,
        public_input: dict[str, Any],
        allow_initial_fallback: bool = False,
    ) -> Any:
        target = TypeAdapter(output_type)
        envelope = await self._invoke(
            output_type=_JsonDocument,
            schema_title=f"{schema_title}Document",
            stage=stage,
            prompt=_stage_prompt(
                stage,
                {
                    **public_input,
                    "target_schema": target.json_schema(),
                },
            ),
            allow_initial_fallback=allow_initial_fallback,
        )
        try:
            return target.validate_json(envelope.document)
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GeminiAnalysisError(
                "gemini_validation_failed",
                "Gemini 구조화 분석 결과 검증에 실패했습니다.",
            ) from error

    async def _invoke(
        self,
        *,
        output_type: Any,
        schema_title: str,
        stage: str,
        prompt: str,
        allow_initial_fallback: bool = False,
    ) -> Any:
        if self._api_key is None:
            raise GeminiAnalysisError(
                "gemini_not_configured",
                "Gemini API Key가 설정되지 않았습니다.",
            )
        try:
            return await self._invoke_model(
                self._selected_model,
                output_type=output_type,
                schema_title=schema_title,
                stage=stage,
                prompt=prompt,
            )
        except asyncio.CancelledError:
            raise
        except GeminiAnalysisError:
            raise
        except Exception as error:
            if (
                allow_initial_fallback
                and self._selected_model == self._primary_model
                and self._fallback_model != self._primary_model
                and _is_typed_not_found(error)
            ):
                self._selected_model = self._fallback_model
                try:
                    return await self._invoke_model(
                        self._selected_model,
                        output_type=output_type,
                        schema_title=schema_title,
                        stage=stage,
                        prompt=prompt,
                    )
                except asyncio.CancelledError:
                    raise
                except GeminiAnalysisError:
                    raise
                except Exception as fallback_error:
                    raise _public_provider_error(fallback_error) from fallback_error
            raise _public_provider_error(error) from error

    async def _invoke_model(
        self,
        model_name: str,
        *,
        output_type: Any,
        schema_title: str,
        stage: str,
        prompt: str,
    ) -> Any:
        model = self._get_model(model_name)
        adapter: TypeAdapter[Any] = TypeAdapter(output_type)
        schema = adapter.json_schema()
        schema["title"] = schema_title
        chain = model.with_structured_output(schema, method="json_schema")
        config = build_langfuse_config(
            run_name=f"customer_signal.{stage}",
            provider="gemini",
            stage=stage,
        )
        config["metadata"]["schema_title"] = schema_title
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw = await chain.ainvoke(prompt, config=config)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise GeminiAnalysisError(
                "gemini_timeout",
                "Gemini 구조화 분석 단계가 제한 시간을 초과했습니다.",
            ) from error
        try:
            return _validate_structured_value(adapter, raw)
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GeminiAnalysisError(
                "gemini_validation_failed",
                "Gemini 구조화 분석 결과 검증에 실패했습니다.",
            ) from error

    def _get_model(self, model_name: str) -> Any:
        model = self._models.get(model_name)
        if model is None:
            model = self._model_factory(
                model=model_name,
                api_key=self._api_key,
                retries=0,
                request_timeout=self._timeout_seconds,
            )
            self._models[model_name] = model
        return model


def _validate_structured_value(adapter: TypeAdapter[T], raw: Any) -> T:
    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json")
    if isinstance(raw, str):
        return adapter.validate_json(raw)
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    return adapter.validate_json(encoded)


def _public_manifests(manifests: Sequence[SourceManifest]) -> list[dict[str, Any]]:
    return [
        PublicSourceManifest.from_internal(manifest).model_dump(mode="json")
        for manifest in manifests
    ]


def _primitive_catalog() -> dict[str, Any]:
    return {
        "names": [
            "catalog_sources",
            "profile_events",
            "aggregate_events",
            "segment_customers",
            "detect_repetition",
            "match_sequence",
            "compare_segments",
            "rank_customers",
            "get_customer_journey",
            "get_evidence",
        ],
        "input_schema": PRIMITIVE_INPUT_ADAPTER.json_schema(),
        # match_sequence가 실제로 해석하는 토큰 어휘 — analytics.primitives.
        # sequences._matches_token과 동기화된 도구 문서. 임의로 지어낸 이벤트
        # 이름은 어떤 이벤트와도 매칭되지 않는다.
        "sequence_event_aliases": [
            "search_failed",
            "repeat_behavior",
            "support_contact",
            "negative_feedback",
            "unresolved_voc",
            "signup_started",
            "signup_completed",
        ],
        "sequence_token_syntax": (
            "sequence 항목은 위 alias, '<event_type>:<action|outcome|topic>' "
            "표기, 또는 predicate 표현식(예: \"outcome == 'negative'\")만 "
            "사용한다. '반복 행동 뒤 상담'은 [repeat_behavior, "
            "support_contact], '가입 시작 후 미완료'는 [signup_started, "
            "signup_completed] 매칭 뒤 미완료 segment 분리로 계획한다"
        ),
        "aggregate_usage": (
            "aggregate_events로 '~한 고객 수'를 셀 때는 질문의 한정어를 "
            "predicates로 넣는다 (예: 부정 피드백 → \"outcome == "
            "'negative'\") — predicate 없는 집계는 전체 모집단을 세게 된다"
        ),
    }


def _stage_prompt(stage: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "stage": stage,
            "instruction": (
                "Return one envelope whose document field is a JSON string matching "
                "input.target_schema, with no additional prose. Apply every rule in "
                "any *_policy field of input when drafting the document."
            ),
            "input": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_typed_not_found(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    for _depth in range(8):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        for attribute in ("code", "status", "status_code"):
            value = getattr(current, attribute, None)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            candidates = [value, getattr(value, "name", None), getattr(value, "value", None)]
            if any(candidate == 404 for candidate in candidates):
                return True
            if any(
                isinstance(candidate, str)
                and candidate.strip().upper() in {"404", "NOT_FOUND"}
                for candidate in candidates
            ):
                return True
        nested = current.__cause__ or current.__context__
        current = nested if isinstance(nested, BaseException) else None
    return False


def _public_provider_error(error: Exception) -> GeminiAnalysisError:
    if _is_typed_not_found(error):
        return GeminiAnalysisError(
            "gemini_model_not_found",
            "사용 가능한 Gemini 구조화 분석 모델을 찾지 못했습니다.",
        )
    return GeminiAnalysisError(
        "gemini_provider_failed",
        "Gemini 구조화 분석 서비스 호출에 실패했습니다.",
    )


__all__ = ["GeminiAnalysisError", "GeminiAnalysisModel"]
