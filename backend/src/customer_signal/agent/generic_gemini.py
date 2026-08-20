"""Staged Gemini model adapter for the validated generic Analysis Loop."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, TypeAdapter, ValidationError

from customer_signal.agent.contracts import (
    ReportModelContext,
    RunRequest,
    SelectionContext,
    StepModelContext,
)
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    CustomerSignalReportDraft,
    GoalDecision,
    StepSelection,
)
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.sources import PublicSourceManifest, SourceManifest


T = TypeVar("T")


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
        prompt = _stage_prompt(
            "goal",
            {
                "request": request.model_dump(mode="json"),
                "allowed_sources": _public_manifests(manifests),
                "constraints": {
                    "narrow_scope_only": True,
                    "clarify_ambiguous_request": True,
                    "reject_pii_raw_export_and_writes": True,
                },
            },
        )
        return await self._invoke(
            output_type=GoalDecision,
            schema_title="GoalDecision",
            prompt=prompt,
            allow_initial_fallback=True,
        )

    async def create_plan(
        self,
        goal: AnalysisGoal,
        manifests: list[SourceManifest],
    ) -> AnalysisPlan:
        prompt = _stage_prompt(
            "plan",
            {
                "validated_goal": goal.model_dump(mode="json"),
                "allowed_sources": _public_manifests(manifests),
                "constraints": {
                    "step_count": {"minimum": 3, "maximum": 6},
                    "topological_dependencies_only": True,
                    "use_declared_capabilities_only": True,
                },
            },
        )
        return await self._invoke(
            output_type=AnalysisPlan,
            schema_title="AnalysisPlan",
            prompt=prompt,
        )

    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft:
        prompt = _stage_prompt(
            "note_draft",
            {
                "validated_goal": context.goal.model_dump(mode="json"),
                "validated_plan": context.plan.model_dump(mode="json"),
                "completed_step": context.step.model_dump(mode="json"),
                "current_fact_id": context.current_fact.fact_id,
                "verified_facts": [_public_fact(fact) for fact in context.facts],
                "constraints": {
                    "claims_must_exactly_reference_verified_facts": True,
                    "no_new_numeric_or_identity_values": True,
                },
            },
        )
        return await self._invoke(
            output_type=AnalysisNoteDraft,
            schema_title="AnalysisNoteDraft",
            prompt=prompt,
        )

    async def select_next(self, context: SelectionContext) -> StepSelection:
        prompt = _stage_prompt(
            "step_selection",
            {
                "validated_goal": context.goal.model_dump(mode="json"),
                "validated_plan": context.plan.model_dump(mode="json"),
                "completed_step_ids": sorted(context.completed_step_ids),
                "verified_facts": [_public_fact(fact) for fact in context.facts],
                "constraints": {
                    "select_only_validated_ready_step": True,
                    "completed_steps_are_immutable": True,
                },
            },
        )
        return await self._invoke(
            output_type=StepSelection,
            schema_title="StepSelection",
            prompt=prompt,
        )

    async def create_report(
        self,
        context: ReportModelContext,
    ) -> CustomerSignalReportDraft:
        prompt = _stage_prompt(
            "report_draft",
            {
                "validated_goal": context.goal.model_dump(mode="json"),
                "validated_plan": context.plan.model_dump(mode="json"),
                "verified_facts": [_public_fact(fact) for fact in context.facts],
                "verified_notes": [_public_note(note) for note in context.notes],
                "constraints": {
                    "select_verified_claim_ids_only": True,
                    "actions_require_claim_and_fact_refs": True,
                    "server_composes_all_public_text": True,
                },
            },
        )
        return await self._invoke(
            output_type=CustomerSignalReportDraft,
            schema_title="CustomerSignalReportDraft",
            prompt=prompt,
        )

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


def _public_fact(fact: AnalysisFact) -> dict[str, Any]:
    """Projection for model context that deliberately omits nested evidence records."""

    return {
        "fact_id": fact.fact_id,
        "step_id": fact.step_id,
        "primitive": fact.primitive,
        "result_id": fact.result_id,
        "source_ids": list(fact.source_ids),
        "customer_ids": list(fact.customer_ids),
        "evidence_ids": list(fact.evidence_ids),
        "metrics": [metric.model_dump(mode="json") for metric in fact.metrics],
        "processing": fact.payload.processing.model_dump(mode="json"),
        "created_at": fact.created_at.isoformat(),
    }


def _public_note(note) -> dict[str, Any]:
    return {
        "note_id": note.note_id,
        "step_id": note.step_id,
        "fact_ids": list(note.fact_ids),
        "claims": [claim.model_dump(mode="json") for claim in note.claims],
        "limitations": list(note.limitations),
        "plan_revision": note.plan_revision,
    }


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
