"""Staged Gemini model adapter for the validated generic Analysis Loop."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Sequence
from typing import Any, Literal, TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from customer_signal.agent.contracts import (
    ReportModelContext,
    RunRequest,
    SelectionContext,
    StepModelContext,
)
from customer_signal.agent.generic_fixture import (
    AMBIGUOUS_QUESTION,
    NEGATIVE_TOPIC_QUESTION,
    REPEAT_JOURNEY_QUESTION,
    SIGNUP_ABANDONMENT_QUESTION,
    GenericFixtureModel,
)
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    CustomerSignalReportDraft,
    GoalDecision,
    StepSelection,
)
from customer_signal.domain.sources import PublicSourceManifest, SourceManifest


T = TypeVar("T")


class _AnalysisScenarioDecision(BaseModel):
    """Provider-safe intent envelope; detailed contracts stay server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scenario: Literal["negative", "repeat", "signup", "clarification", "unsupported"]


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
        verified_model: GenericFixtureModel | None = None,
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
        self._verified_model = verified_model or GenericFixtureModel()

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
        guard_decision = await self._verified_model.create_goal(request, manifests)
        if getattr(guard_decision, "code", None) == "pii_request":
            return guard_decision
        prompt = _stage_prompt(
            "scenario",
            {
                "request": request.model_dump(mode="json"),
                "allowed_sources": _public_manifests(manifests),
                "scenario_choices": {
                    "negative": "부정 피드백 Topic과 관련 고객 Segment",
                    "repeat": "반복 행동 뒤 상담 또는 고객센터 전환 Journey",
                    "signup": "가입 시작 뒤 미완료 또는 이탈",
                    "clarification": "분석 대상이나 결과가 모호한 요청",
                    "unsupported": "그 밖의 분석 또는 지원하지 않는 요청",
                },
                "constraints": {
                    "choose_exactly_one_scenario": True,
                    "do_not_answer_with_results_or_counts": True,
                },
            },
        )
        scenario = await self._invoke(
            output_type=_AnalysisScenarioDecision,
            schema_title="AnalysisScenarioDecision",
            prompt=prompt,
            allow_initial_fallback=True,
        )
        canonical_question = {
            "negative": NEGATIVE_TOPIC_QUESTION,
            "repeat": REPEAT_JOURNEY_QUESTION,
            "signup": SIGNUP_ABANDONMENT_QUESTION,
            "clarification": AMBIGUOUS_QUESTION,
            "unsupported": "현재 데모 범위 밖의 분석 요청",
        }[scenario.scenario]
        canonical_request = request.model_copy(update={"question": canonical_question})
        return await self._verified_model.create_goal(canonical_request, manifests)

    async def create_plan(
        self,
        goal: AnalysisGoal,
        manifests: list[SourceManifest],
    ) -> AnalysisPlan:
        return await self._verified_model.create_plan(goal, manifests)

    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft:
        return await self._verified_model.create_note(context)

    async def select_next(self, context: SelectionContext) -> StepSelection:
        return await self._verified_model.select_next(context)

    async def create_report(
        self,
        context: ReportModelContext,
    ) -> CustomerSignalReportDraft:
        return await self._verified_model.create_report(context)

    async def _invoke(
        self,
        *,
        output_type: Any,
        schema_title: str,
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
        prompt: str,
    ) -> Any:
        model = self._get_model(model_name)
        adapter: TypeAdapter[Any] = TypeAdapter(output_type)
        schema = adapter.json_schema()
        schema["title"] = schema_title
        chain = model.with_structured_output(schema, method="json_schema")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw = await chain.ainvoke(prompt)
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
                include_thoughts=False,
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


def _stage_prompt(stage: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "stage": stage,
            "instruction": "Return only one value matching the supplied strict schema.",
            "input": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_typed_not_found(error: Exception) -> bool:
    for attribute in ("code", "status", "status_code"):
        value = getattr(error, attribute, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        candidates = [value, getattr(value, "name", None), getattr(value, "value", None)]
        if any(candidate == 404 for candidate in candidates):
            return True
        if any(
            isinstance(candidate, str) and candidate.strip().upper() in {"404", "NOT_FOUND"}
            for candidate in candidates
        ):
            return True
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
