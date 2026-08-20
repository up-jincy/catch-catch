"""Staged structured-output and provider-boundary tests for generic Gemini."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from customer_signal.agent.analysis_loop import AnalysisLoop
from customer_signal.agent.claim_validator import render_verified_note
from customer_signal.agent.contracts import (
    ReportModelContext,
    RunRequest,
    SelectionContext,
    StepModelContext,
)
from customer_signal.agent.generic_fixture import (
    NEGATIVE_TOPIC_QUESTION,
    GenericFixtureModel,
)
from customer_signal.agent.generic_gemini import (
    GeminiAnalysisError,
    GeminiAnalysisModel,
)
from customer_signal.domain.facts import (
    AnalysisMetricFact,
    AnalysisSourceCatalogFact,
    CatalogSourcesPayload,
    FactProvenance,
    ProcessingStats,
    build_fact,
)
from customer_signal.domain.sources import (
    DimensionDescriptor,
    EventScope,
    IdentityQualityDescriptor,
    MaskingPolicy,
    SourceManifest,
    TimeRange,
)
from customer_signal.domain.types import GenericPrimitiveName


NOW = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
ALL_PRIMITIVES: frozenset[GenericPrimitiveName] = frozenset(
    {
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
    }
)


def _request() -> RunRequest:
    return RunRequest(
        question=NEGATIVE_TOPIC_QUESTION,
        start_at=NOW - timedelta(days=30),
        end_at=NOW,
        enabled_sources=["voc"],
    )


def _manifest() -> SourceManifest:
    return SourceManifest(
        source_id="voc",
        label="VOC",
        description="Masked support signals",
        adapter_version="adapter-1",
        manifest_version="manifest-1",
        data_interval=TimeRange(
            start_at=NOW - timedelta(days=365),
            end_at=NOW + timedelta(days=1),
        ),
        refresh_cadence="static_demo",
        supported_event_types=frozenset({"voc"}),
        supported_topics=frozenset({"quality"}),
        supported_outcomes=frozenset({"negative"}),
        dimensions={
            "email": DimensionDescriptor(
                semantic_type="identifier",
                description="private direct identity",
                pii_classification="direct_identifier",
            )
        },
        measures={},
        capabilities=ALL_PRIMITIVES,
        masking_policy=MaskingPolicy(rules={"email": "hash"}),
        identity_quality=IdentityQualityDescriptor(
            namespace="private-email-namespace",
            link_method="synthetic",
            confidence=1.0,
        ),
    )


def _catalog_fact(scope: EventScope):
    payload = CatalogSourcesPayload(
        kind="catalog_sources",
        input_fact_ids=[],
        processing=ProcessingStats(
            scanned_events=1,
            matched_events=1,
            returned_rows=1,
        ),
        provenance=FactProvenance(
            scope=scope,
            source_ids=["voc"],
            adapter_versions={"voc": "adapter-1"},
            manifest_versions={"voc": "manifest-1"},
            dataset_version="dataset-1",
        ),
        metrics=[
            AnalysisMetricFact(
                metric_key="source_count",
                label="Source Count",
                value=1,
                unit="sources",
            )
        ],
        sources=[
            AnalysisSourceCatalogFact(
                source_id="voc",
                data_interval=TimeRange(start_at=scope.start_at, end_at=scope.end_at),
                row_count=1,
                manifest_version="manifest-1",
            )
        ],
    )
    return build_fact(
        fact_id="fact-catalog",
        step_id="step-catalog",
        primitive="catalog_sources",
        result_id="catalog_sources:fact-catalog",
        payload=payload,
        scope=scope,
        created_at=NOW,
    )


class _ScriptedProvider:
    def __init__(self, responses_by_model: dict[str, list[Any]]) -> None:
        self.responses = defaultdict(list)
        for model_name, responses in responses_by_model.items():
            self.responses[model_name].extend(responses)
        self.model_calls: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any):
        self.model_calls.append(dict(kwargs))
        return _ScriptedChat(self, kwargs["model"])


class _ScriptedChat:
    def __init__(self, provider: _ScriptedProvider, model_name: str) -> None:
        self.provider = provider
        self.model_name = model_name

    def with_structured_output(self, schema, **kwargs: Any):
        call = {
            "model": self.model_name,
            "schema": schema,
            "schema_title": schema.get("title"),
            **kwargs,
        }
        self.provider.structured_calls.append(call)
        return _ScriptedChain(self.provider, self.model_name, call)


class _ScriptedChain:
    def __init__(
        self,
        provider: _ScriptedProvider,
        model_name: str,
        call: dict[str, Any],
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.call = call

    async def ainvoke(self, prompt: str):
        self.call["prompt"] = prompt
        response = self.provider.responses[self.model_name].pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return await response()
        return response


class _ModelNotFoundError(RuntimeError):
    code = "NOT_FOUND"


async def _never_returns():
    await asyncio.Future()


async def _staged_values():
    fixture = GenericFixtureModel()
    request = _request()
    manifests = [_manifest()]
    goal = await fixture.create_goal(request, manifests)
    assert goal.kind == "goal"
    plan = await fixture.create_plan(goal, manifests)
    scope = EventScope(
        start_at=request.start_at,
        end_at=request.end_at,
        source_ids=["voc"],
        max_events=100,
    )
    fact = _catalog_fact(scope)
    step_context = StepModelContext(
        goal=goal,
        plan=plan,
        step=plan.steps[0],
        facts=[fact],
        current_fact=fact,
    )
    note_draft = await fixture.create_note(step_context)
    note = render_verified_note(note_draft, fact, 1, plan_revision=plan.revision)
    selection_context = SelectionContext(
        goal=goal,
        plan=plan,
        completed_step_ids=frozenset({plan.steps[0].step_id}),
        facts=[fact],
    )
    selection = await fixture.select_next(selection_context)
    report_context = ReportModelContext(
        goal=goal,
        plan=plan,
        facts=[fact],
        notes=[note],
    )
    report_draft = await fixture.create_report(report_context)
    return (
        request,
        manifests,
        goal,
        plan,
        step_context,
        note_draft,
        selection_context,
        selection,
        report_context,
        report_draft,
    )


@pytest.mark.asyncio
async def test_uses_five_strict_structured_stages_and_public_prompts() -> None:
    (
        request,
        manifests,
        goal,
        plan,
        step_context,
        note_draft,
        selection_context,
        selection,
        report_context,
        report_draft,
    ) = await _staged_values()
    provider = _ScriptedProvider(
        {
            "gemini-3.7-flash": [goal, plan, note_draft, selection, report_draft],
        }
    )
    model = GeminiAnalysisModel(
        api_key="provider-secret-key",
        model_factory=provider,
        timeout_seconds=0.25,
    )

    assert await model.create_goal(request, manifests) == goal
    assert await model.create_plan(goal, manifests) == plan
    assert await model.create_note(step_context) == note_draft
    assert await model.select_next(selection_context) == selection
    assert await model.create_report(report_context) == report_draft

    assert [call["schema_title"] for call in provider.structured_calls] == [
        "GoalDecision",
        "AnalysisPlan",
        "AnalysisNoteDraft",
        "StepSelection",
        "CustomerSignalReportDraft",
    ]
    assert all(call["method"] == "json_schema" for call in provider.structured_calls)
    assert provider.model_calls == [
        {
            "model": "gemini-3.7-flash",
            "api_key": "provider-secret-key",
            "retries": 0,
            "request_timeout": 0.25,
            "include_thoughts": False,
        }
    ]
    public_prompts = "\n".join(call["prompt"] for call in provider.structured_calls)
    assert "provider-secret-key" not in public_prompts
    assert "private-email-namespace" not in public_prompts
    assert '"email"' not in public_prompts
    assert '"payload"' not in public_prompts
    assert "chain_of_thought" not in public_prompts
    assert model.model_name == "gemini-3.7-flash"


@pytest.mark.asyncio
async def test_typed_not_found_on_first_stage_switches_once_to_36() -> None:
    request, manifests, goal, plan, *_rest = await _staged_values()
    provider = _ScriptedProvider(
        {
            "gemini-3.7-flash": [_ModelNotFoundError("private primary response")],
            "gemini-3.6-flash": [goal, plan],
        }
    )
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    assert await model.create_goal(request, manifests) == goal
    assert await model.create_plan(goal, manifests) == plan

    assert [call["model"] for call in provider.model_calls] == [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ]
    assert model.model_name == "gemini-3.6-flash"


@pytest.mark.asyncio
async def test_not_found_after_goal_never_falls_back() -> None:
    request, manifests, goal, *_rest = await _staged_values()
    provider = _ScriptedProvider(
        {
            "gemini-3.7-flash": [goal, _ModelNotFoundError("private plan response")],
            "gemini-3.6-flash": [],
        }
    )
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    await model.create_goal(request, manifests)
    with pytest.raises(GeminiAnalysisError) as caught:
        await model.create_plan(goal, manifests)

    assert caught.value.code == "gemini_model_not_found"
    assert "private" not in str(caught.value).casefold()
    assert [call["model"] for call in provider.model_calls] == ["gemini-3.7-flash"]


@pytest.mark.asyncio
async def test_timeout_is_safe_and_external_cancellation_is_preserved() -> None:
    request, manifests, *_rest = await _staged_values()
    timeout_provider = _ScriptedProvider({"gemini-3.7-flash": [_never_returns]})
    timeout_model = GeminiAnalysisModel(
        api_key="key",
        model_factory=timeout_provider,
        timeout_seconds=0.01,
    )

    with pytest.raises(GeminiAnalysisError) as caught:
        await timeout_model.create_goal(request, manifests)
    assert caught.value.code == "gemini_timeout"

    cancel_provider = _ScriptedProvider({"gemini-3.7-flash": [_never_returns]})
    cancel_model = GeminiAnalysisModel(
        api_key="key",
        model_factory=cancel_provider,
        timeout_seconds=1,
    )
    task = asyncio.create_task(cancel_model.create_goal(request, manifests))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class _NeverExecutor:
    calls = 0

    async def execute_async(self, *args: Any, **kwargs: Any):
        self.calls += 1
        raise AssertionError("provider failure must happen before primitive execution")


@pytest.mark.asyncio
async def test_provider_failure_becomes_generic_safe_failure_without_fixture_fallback() -> None:
    provider = _ScriptedProvider(
        {"gemini-3.7-flash": [RuntimeError("private provider transcript and secret")]}
    )
    executor = _NeverExecutor()
    loop = AnalysisLoop(
        model=GeminiAnalysisModel(api_key="key", model_factory=provider),
        executor=executor,
        manifests=[_manifest()],
    )
    events = []

    outcome = await loop.run(_request(), emit=events.append)

    assert outcome.outcome_kind == "generic"
    assert outcome.status == "failed"
    assert outcome.report is None
    assert outcome.error is not None
    assert outcome.error.code == "gemini_provider_failed"
    assert executor.calls == 0
    public = json.dumps(
        [event.model_dump(mode="json") for event in events],
        ensure_ascii=False,
    ).casefold()
    assert "private provider" not in public
    assert "secret" not in public


@pytest.mark.asyncio
async def test_malformed_structured_value_fails_closed() -> None:
    request, manifests, *_rest = await _staged_values()
    provider = _ScriptedProvider({"gemini-3.7-flash": [{"kind": "goal", "source_ids": ["forged"]}]})
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    with pytest.raises(GeminiAnalysisError) as caught:
        await model.create_goal(request, manifests)

    assert caught.value.code == "gemini_validation_failed"
