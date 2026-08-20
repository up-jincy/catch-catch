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
from customer_signal.domain.primitives import PRIMITIVE_INPUT_ADAPTER
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
FREE_QUESTION = "부정 피드백 고객은 이후 어떤 행동을 보이고 일반 고객과 무엇이 달라?"
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


def _request(question: str = FREE_QUESTION) -> RunRequest:
    return RunRequest(
        question=question,
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

    async def ainvoke(self, prompt: str, config: dict[str, Any] | None = None):
        self.call["prompt"] = prompt
        self.call["invoke_config"] = config
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


async def _staged_values(*, question: str = FREE_QUESTION):
    fixture = GenericFixtureModel()
    request = _request(question)
    manifests = [_manifest()]
    goal = await fixture.create_goal(
        request.model_copy(update={"question": NEGATIVE_TOPIC_QUESTION}),
        manifests,
    )
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
async def test_free_question_uses_five_flat_provider_documents() -> None:
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
            "gemini-3.7-flash": [
                {"document": goal.model_dump_json()},
                {"document": plan.model_dump_json()},
                {"document": note_draft.model_dump_json()},
                {"document": selection.model_dump_json()},
                {"document": report_draft.model_dump_json()},
            ],
        }
    )
    model = GeminiAnalysisModel(
        api_key="provider-secret-key",
        model_factory=provider,
        timeout_seconds=0.25,
    )

    assert await model.create_goal(request, manifests) == goal
    assert (
        await model.create_plan(
            goal,
            manifests,
            validation_feedback="첫 단계에서 Source 범위를 확인하세요.",
        )
        == plan
    )
    assert await model.create_note(step_context) == note_draft
    assert await model.select_next(selection_context) == selection
    assert await model.create_report(report_context) == report_draft

    assert [call["schema_title"] for call in provider.structured_calls] == [
        "GoalDecisionDocument",
        "AnalysisPlanDocument",
        "AnalysisNoteDraftDocument",
        "StepSelectionDocument",
        "CustomerSignalReportDraftDocument",
    ]
    assert all(call["method"] == "json_schema" for call in provider.structured_calls)
    for call in provider.structured_calls:
        schema = call["schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {"document"}
        assert schema["required"] == ["document"]
        serialized_schema = json.dumps(schema, sort_keys=True)
        assert all(
            unsupported not in serialized_schema
            for unsupported in ("$defs", "$ref", "oneOf", "discriminator")
        )
    assert provider.model_calls == [
        {
            "model": "gemini-3.7-flash",
            "api_key": "provider-secret-key",
            "retries": 0,
            "request_timeout": 0.25,
        }
    ]
    prompts = [json.loads(call["prompt"]) for call in provider.structured_calls]
    assert [prompt["stage"] for prompt in prompts] == [
        "goal",
        "plan",
        "note",
        "selection",
        "report",
    ]
    assert [call["invoke_config"] for call in provider.structured_calls] == [
        {
            "run_name": f"customer_signal.{stage}",
            "tags": ["customer-signal", "gemini", stage],
            "metadata": {
                "provider": "gemini",
                "stage": stage,
                "schema_title": schema_title,
            },
        }
        for stage, schema_title in (
            ("goal", "GoalDecisionDocument"),
            ("plan", "AnalysisPlanDocument"),
            ("note", "AnalysisNoteDraftDocument"),
            ("selection", "StepSelectionDocument"),
            ("report", "CustomerSignalReportDraftDocument"),
        )
    ]
    goal_input = prompts[0]["input"]
    plan_input = prompts[1]["input"]
    note_input = prompts[2]["input"]
    report_input = prompts[4]["input"]
    assert goal_input["request"]["question"] == request.question
    assert goal_input["sources"][0]["description"] == "Masked support signals"
    assert plan_input["sources"] == goal_input["sources"]
    assert plan_input["validation_feedback"] == "첫 단계에서 Source 범위를 확인하세요."
    expected_primitive_names = [
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
    ]
    assert goal_input["primitive_catalog"]["names"] == expected_primitive_names
    assert plan_input["primitive_catalog"]["names"] == expected_primitive_names
    assert (
        goal_input["primitive_catalog"]["input_schema"]
        == PRIMITIVE_INPUT_ADAPTER.json_schema()
    )
    assert plan_input["constraints"] == {
        "compare_segments": (
            "two dependencies must both publish parameters.metric_key; "
            "required output is <metric_key>_delta"
        ),
        "dependency_arity": {
            "catalog_sources": {"maximum": 0, "minimum": 0},
            "profile_events": {"maximum": 0, "minimum": 0},
            "aggregate_events": {"maximum": 0, "minimum": 0},
            "segment_customers": {"maximum": 0, "minimum": 0},
            "detect_repetition": {"maximum": 0, "minimum": 0},
            "match_sequence": {"maximum": 0, "minimum": 0},
            "compare_segments": {"maximum": 2, "minimum": 2},
            "rank_customers": {"maximum": 4, "minimum": 1},
            "get_customer_journey": {"maximum": 1, "minimum": 1},
            "get_evidence": {"maximum": 1, "minimum": 1},
        },
        "first_step_should_discover_sources": True,
        "initial_revision": 0,
        "input_step_ids": (
            "must obey dependency_arity bounds and reference prior steps only"
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
        "step_count": "3..6",
    }
    assert note_input["claim_constraints"] == {
        "cardinality": "each Claim must bind exactly one Fact through exactly one FactRef",
        "fact_ref_binding": {
            "fact_id": step_context.current_fact.fact_id,
            "result_id": step_context.current_fact.result_id,
            "plan_revision": step_context.plan.revision,
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
    }
    verified_claim_ids = [
        claim.claim_id for note in report_context.notes for claim in note.claims
    ]
    assert report_input["report_constraints"] == {
        "goal_id": {"required_exact": report_context.goal.goal_id},
        "claim_refs": {
            "allowed_values": verified_claim_ids,
            "rule": "subset only",
        },
        "recommended_actions.fact_refs": {
            "allowed_values": [fact.fact_id for fact in report_context.facts],
            "rule": "subset only",
        },
        "recommended_actions.claim_refs": {
            "rule": "subset of selected top-level claim_refs only",
        },
    }

    public_prompts = "\n".join(call["prompt"] for call in provider.structured_calls)
    assert request.question in public_prompts
    assert NEGATIVE_TOPIC_QUESTION not in public_prompts
    assert "provider-secret-key" not in public_prompts
    assert "private-email-namespace" not in public_prompts
    assert '"email"' not in public_prompts
    assert "private direct identity" not in public_prompts
    assert "provider_response" not in public_prompts
    assert "private provider transcript" not in public_prompts
    assert "chain_of_thought" not in public_prompts
    assert model.model_name == "gemini-3.7-flash"


@pytest.mark.asyncio
async def test_typed_not_found_on_first_stage_switches_once_to_36() -> None:
    request, manifests, goal, plan, *_rest = await _staged_values()
    provider = _ScriptedProvider(
        {
            "gemini-3.7-flash": [_ModelNotFoundError("private primary response")],
            "gemini-3.6-flash": [
                {"document": goal.model_dump_json()},
                {"document": plan.model_dump_json()},
            ],
        }
    )
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    assert await model.create_goal(request, manifests) == goal
    assert await model.create_plan(goal, manifests) == plan

    assert [call["model"] for call in provider.model_calls] == [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ]
    assert [call["model"] for call in provider.structured_calls] == [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.6-flash",
    ]
    assert model.model_name == "gemini-3.6-flash"


@pytest.mark.asyncio
async def test_wrapped_typed_not_found_on_first_stage_switches_to_36() -> None:
    request, manifests, goal, *_rest = await _staged_values()
    wrapped_error = RuntimeError("public wrapper")
    wrapped_error.__cause__ = _ModelNotFoundError("private primary response")
    provider = _ScriptedProvider(
        {
            "gemini-3.7-flash": [wrapped_error],
            "gemini-3.6-flash": [{"document": goal.model_dump_json()}],
        }
    )
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    assert await model.create_goal(request, manifests) == goal
    assert [call["model"] for call in provider.model_calls] == [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ]
    assert model.model_name == "gemini-3.6-flash"


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
async def test_invalid_domain_document_fails_with_safe_validation_error() -> None:
    request, manifests, *_rest = await _staged_values()
    provider = _ScriptedProvider({"gemini-3.7-flash": [{"document": "{}"}]})
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    with pytest.raises(GeminiAnalysisError) as caught:
        await model.create_goal(request, manifests)

    assert caught.value.code == "gemini_validation_failed"


@pytest.mark.asyncio
async def test_pii_request_is_rejected_before_provider_invocation() -> None:
    provider = _ScriptedProvider({"gemini-3.7-flash": []})
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    decision = await model.create_goal(
        _request("고객 이메일 원본을 모두 export해 줘"),
        [_manifest()],
    )

    assert decision.kind == "unsupported"
    assert decision.code == "pii_request"
    assert provider.model_calls == []
