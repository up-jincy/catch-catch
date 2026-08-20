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
                "constraints": {
                    "step_count": "3..6",
                    "first_step_should_discover_sources": True,
                    "read_only": True,
                },
            },
        )

    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft:
        return await self._invoke_document(
            output_type=AnalysisNoteDraft,
            schema_title="AnalysisNoteDraft",
            stage="note",
            public_input={"context": context.model_dump(mode="json")},
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
            public_input={"context": context.model_dump(mode="json")},
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
        config = {
            "run_name": f"customer_signal.{stage}",
            "tags": ["customer-signal", "gemini", stage],
            "metadata": {
                "provider": "gemini",
                "stage": stage,
                "schema_title": schema_title,
            },
        }
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
    }


def _stage_prompt(stage: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "stage": stage,
            "instruction": (
                "Return one envelope whose document field is a JSON string matching "
                "input.target_schema, with no additional prose."
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
