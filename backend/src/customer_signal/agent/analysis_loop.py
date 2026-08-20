"""Validated orchestration for generic customer-signal analysis.

The model may propose typed Goals, Plans, Notes, and report references.  The
server remains the publication boundary: it validates the scope and Plan,
executes primitives, binds every Claim to a Fact, and composes the report.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Protocol, cast

from customer_signal.agent.claim_validator import render_verified_note
from customer_signal.agent.contracts import (
    AnalysisEvent,
    AnalysisEventEmitter,
    GenericRunnerOutcome,
    ReportModelContext,
    RunRequest,
    SelectionContext,
    StepModelContext,
)
from customer_signal.agent.plan_validator import (
    PlanValidationError,
    validate_fact_against_step,
    validate_goal_against_request,
    validate_plan,
    validate_plan_revision,
)
from customer_signal.agent.report_composer import compose_customer_signal_report
from customer_signal.analytics.executor import RunBudget
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNoteDraft,
    AnalysisPlan,
    AnalysisStep,
    ClarificationRequired,
    ContinueSelection,
    CustomerSignalReportDraft,
    GoalDecision,
    PublicRunError,
    ReviseSelection,
    StepSelection,
    StopOnEmpty,
    StopOnMetric,
    StopSelection,
    UnsupportedAnalysis,
)
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.sources import EventScope, SourceManifest


class AnalysisModel(Protocol):
    """Five typed model stages accepted by the server-owned loop."""

    agent_mode: str
    model_name: str | None

    async def create_goal(
        self,
        request: RunRequest,
        manifests: list[SourceManifest],
    ) -> GoalDecision: ...

    async def create_plan(
        self,
        goal: AnalysisGoal,
        manifests: list[SourceManifest],
        *,
        validation_feedback: str | None = None,
    ) -> AnalysisPlan: ...

    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft: ...

    async def select_next(self, context: SelectionContext) -> StepSelection: ...

    async def create_report(
        self,
        context: ReportModelContext,
    ) -> CustomerSignalReportDraft: ...


class PrimitiveExecutor(Protocol):
    """Executor boundary shared with ``analytics.executor.PrimitiveExecutor``."""

    async def execute_async(
        self,
        step: AnalysisStep,
        *,
        scope: EventScope,
        prior_facts: list[AnalysisFact],
        budget: RunBudget,
    ) -> AnalysisFact: ...


class ManifestProvider(Protocol):
    def manifests(self, source_ids: Sequence[str]) -> list[SourceManifest]: ...


class AnalysisLoop:
    """Execute one bounded, validated generic analysis and retain safe partial state."""

    def __init__(
        self,
        *,
        model: AnalysisModel,
        executor: PrimitiveExecutor,
        manifests: Sequence[SourceManifest] | None = None,
        registry: ManifestProvider | None = None,
        max_events: int = 10_000,
        timeout_seconds: float = 120.0,
    ) -> None:
        if (manifests is None) == (registry is None):
            raise ValueError("provide exactly one manifest source")
        if not 1 <= max_events <= 10_000:
            raise ValueError("max_events must be between 1 and 10,000")
        if not 0 < timeout_seconds <= 240:
            raise ValueError("timeout_seconds must be positive and bounded")
        self._model = model
        self._executor = executor
        self._manifests = list(manifests) if manifests is not None else None
        self._registry = registry
        self._max_events = max_events
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        request: RunRequest,
        *,
        emit: AnalysisEventEmitter,
    ) -> GenericRunnerOutcome:
        facts: list[AnalysisFact] = []
        notes = []
        goal: AnalysisGoal | None = None
        plan: AnalysisPlan | None = None
        current_step_id: str | None = None
        budget = RunBudget(deadline_monotonic=time.monotonic() + self._timeout_seconds)

        try:
            manifests = self._load_manifests(request)
            decision = await self._model.create_goal(request, manifests)
            if isinstance(decision, ClarificationRequired):
                await _emit(
                    emit,
                    AnalysisEvent(
                        type="clarification_required",
                        payload=decision.model_dump(mode="json"),
                    ),
                )
                return self._outcome(
                    status="awaiting_clarification",
                    clarification=decision,
                )
            if isinstance(decision, UnsupportedAnalysis):
                await _emit(
                    emit,
                    AnalysisEvent(
                        type="unsupported_analysis",
                        payload=decision.model_dump(mode="json"),
                    ),
                )
                error = PublicRunError(
                    code="unsupported_analysis",
                    message="현재 안전한 분석 범위에서 지원하지 않는 요청입니다.",
                    suggested_questions=list(decision.suggested_questions),
                )
                await _emit_error(emit, error)
                return self._outcome(
                    status="failed",
                    unsupported=decision,
                    error=error,
                )

            goal = decision
            validate_goal_against_request(goal, request, manifests)
            await _emit(
                emit,
                AnalysisEvent(
                    type="goal_created",
                    payload={"goal": goal.model_dump(mode="json")},
                ),
            )

            plan = await _create_validated_plan(self._model, goal, manifests)
            await _emit(
                emit,
                AnalysisEvent(
                    type="plan_created",
                    payload={"plan": plan.model_dump(mode="json")},
                ),
            )

            scope = EventScope(
                start_at=goal.time_range.start_at,
                end_at=goal.time_range.end_at,
                source_ids=list(goal.source_ids),
                max_events=self._max_events,
            )
            completed_step_ids: set[str] = set()
            next_step_id = plan.steps[0].step_id

            while next_step_id is not None:
                step = _select_ready_step(plan, next_step_id, completed_step_ids)
                current_step_id = step.step_id
                started = time.monotonic()
                started_at = datetime.now(timezone.utc)
                await _emit(
                    emit,
                    AnalysisEvent(
                        type="step_started",
                        payload={
                            "step_id": step.step_id,
                            "primitive": step.primitive,
                            "selection_reason": step.selection_reason,
                            "started_at": started_at.isoformat(),
                        },
                    ),
                )
                fact = await self._executor.execute_async(
                    step,
                    scope=scope.model_copy(update={"source_ids": list(step.source_ids)}),
                    prior_facts=facts,
                    budget=budget,
                )
                validate_fact_against_step(step, fact)
                _validate_fact_dependencies(step, fact, facts)
                facts.append(fact)
                await _emit(
                    emit,
                    AnalysisEvent(
                        type="fact_created",
                        payload={
                            "step_id": fact.step_id,
                            "fact": fact.model_dump(mode="json"),
                        },
                    ),
                )

                draft = await self._model.create_note(
                    StepModelContext(
                        goal=goal,
                        plan=plan,
                        step=step,
                        facts=list(facts),
                        current_fact=fact,
                    )
                )
                completed_with_current = completed_step_ids | {step.step_id}
                selected_next_step_id: str | None = None
                next_action = "계획한 분석 단계를 모두 완료했습니다."
                revised_plan: AnalysisPlan | None = None

                if _server_stop_requested(step, fact):
                    next_action = "서버 종료 조건을 충족해 분석을 마칩니다."
                else:
                    remaining = [
                        candidate
                        for candidate in plan.steps
                        if candidate.step_id not in completed_with_current
                    ]
                    if remaining:
                        selection = await self._model.select_next(
                            SelectionContext(
                                goal=goal,
                                plan=plan,
                                completed_step_ids=frozenset(completed_with_current),
                                facts=list(facts),
                            )
                        )
                        next_action = selection.reason
                        if isinstance(selection, ContinueSelection):
                            _select_ready_step(
                                plan,
                                selection.next_step_id,
                                completed_with_current,
                            )
                            selected_next_step_id = selection.next_step_id
                        elif isinstance(selection, ReviseSelection):
                            validate_plan_revision(
                                previous=plan,
                                revised=selection.revised_plan,
                                completed_step_ids=completed_with_current,
                                manifests=manifests,
                            )
                            _validate_plan_scope(selection.revised_plan, goal)
                            _select_ready_step(
                                selection.revised_plan,
                                selection.next_step_id,
                                completed_with_current,
                            )
                            revised_plan = selection.revised_plan
                            selected_next_step_id = selection.next_step_id
                        elif not isinstance(selection, StopSelection):  # pragma: no cover
                            raise ValueError("unknown StepSelection kind")

                duration_ms = min(
                    int((time.monotonic() - started) * 1_000),
                    int(step.limits.timeout_seconds * 1_000),
                    40_000,
                )
                note = render_verified_note(
                    draft,
                    fact,
                    duration_ms,
                    facts=facts,
                    next_step_id=selected_next_step_id,
                    next_action=next_action,
                    plan_revision=plan.revision,
                )
                notes.append(note)
                completed_step_ids.add(step.step_id)
                await _emit(
                    emit,
                    AnalysisEvent(
                        type="analysis_note_created",
                        payload={"note": note.model_dump(mode="json")},
                    ),
                )
                await _emit(
                    emit,
                    AnalysisEvent(
                        type="step_completed",
                        payload={
                            "step_id": step.step_id,
                            "status": "completed",
                            "result_ids": [fact.result_id],
                            "duration_ms": note.duration_ms,
                        },
                    ),
                )
                if revised_plan is not None:
                    await _emit(
                        emit,
                        AnalysisEvent(
                            type="plan_revised",
                            payload={"plan": revised_plan.model_dump(mode="json")},
                        ),
                    )
                    plan = revised_plan
                next_step_id = selected_next_step_id

            await _emit(
                emit,
                AnalysisEvent(
                    type="report_validating",
                    payload={
                        "fact_ids": [fact.fact_id for fact in facts],
                        "result_ids": [fact.result_id for fact in facts],
                    },
                ),
            )
            report_draft = await self._model.create_report(
                ReportModelContext(
                    goal=goal,
                    plan=plan,
                    facts=list(facts),
                    notes=list(notes),
                )
            )
            report = compose_customer_signal_report(
                goal=goal,
                facts=facts,
                notes=notes,
                draft=report_draft,
            )
            await _emit(
                emit,
                AnalysisEvent(
                    type="result",
                    payload={
                        "agent_mode": self._model.agent_mode,
                        "report": report.model_dump(mode="json"),
                    },
                ),
            )
            return self._outcome(
                status="completed",
                goal=goal,
                plan=plan,
                facts=facts,
                notes=notes,
                report=report,
            )
        except asyncio.CancelledError:
            budget.cancel()
            raise
        except Exception as error:
            if getattr(error, "code", None) == "no_data_scope":
                limitation = "요청한 Source와 기간에 분석 가능한 이벤트가 없습니다."
                public_error = PublicRunError(
                    code="no_data_scope",
                    message=limitation,
                    step_id=current_step_id,
                )
                await _emit_error(emit, public_error)
                return self._outcome(
                    status="degraded",
                    goal=goal,
                    plan=plan,
                    limitations=[limitation],
                )
            public_error = PublicRunError(
                code=_safe_error_code(error),
                message="분석 단계 검증 또는 실행에 실패했습니다.",
                step_id=current_step_id,
            )
            await _emit_error(emit, public_error)
            return self._outcome(
                status="failed",
                goal=goal,
                plan=plan,
                facts=facts,
                notes=notes,
                error=public_error,
                failed_step_id=(
                    current_step_id
                    if plan is not None and current_step_id in {step.step_id for step in plan.steps}
                    else None
                ),
            )

    def _load_manifests(self, request: RunRequest) -> list[SourceManifest]:
        if self._manifests is not None:
            manifest_by_id = {manifest.source_id: manifest for manifest in self._manifests}
            if len(manifest_by_id) != len(self._manifests):
                raise ValueError("configured manifests must be unique")
            try:
                return [manifest_by_id[source_id] for source_id in request.enabled_sources]
            except KeyError as error:
                raise ValueError("request references a source without a manifest") from error
        assert self._registry is not None
        return self._registry.manifests(request.enabled_sources)

    def _outcome(self, *, status, **values) -> GenericRunnerOutcome:
        return GenericRunnerOutcome(
            status=status,
            agent_mode=cast(str, self._model.agent_mode),
            model=self._model.model_name,
            **values,
        )


async def _emit(emit: AnalysisEventEmitter, event: AnalysisEvent) -> None:
    pending = emit(event)
    if inspect.isawaitable(pending):
        await cast(Awaitable[None], pending)


async def _emit_error(emit: AnalysisEventEmitter, error: PublicRunError) -> None:
    await _emit(
        emit,
        AnalysisEvent(type="error", payload=error.model_dump(mode="json")),
    )


async def _create_validated_plan(
    model: AnalysisModel,
    goal: AnalysisGoal,
    manifests: list[SourceManifest],
) -> AnalysisPlan:
    validation_feedback: str | None = None
    for attempt in range(2):
        if validation_feedback is None:
            plan = await model.create_plan(goal, manifests)
        else:
            plan = await model.create_plan(
                goal,
                manifests,
                validation_feedback=validation_feedback,
            )
        try:
            if plan.goal_id != goal.goal_id:
                raise PlanValidationError("Plan goal_id must equal the validated Goal")
            if plan.revision != 0:
                raise PlanValidationError("initial Plan revision must be 0")
            validate_plan(plan, manifests)
            _validate_plan_scope(plan, goal)
            return plan
        except ValueError as error:
            summary = f"Plan validation failed: {' '.join(str(error).split())}"[:500]
            if attempt == 1:
                raise PlanValidationError(summary) from error
            validation_feedback = summary
    raise AssertionError("bounded Plan repair loop exhausted")


def _validate_plan_scope(plan: AnalysisPlan, goal: AnalysisGoal) -> None:
    goal_sources = set(goal.source_ids)
    if any(not set(step.source_ids) <= goal_sources for step in plan.steps):
        raise ValueError("Plan step cannot expand the validated Goal source scope")


def _select_ready_step(
    plan: AnalysisPlan,
    step_id: str,
    completed_step_ids: set[str],
) -> AnalysisStep:
    step = next((step for step in plan.steps if step.step_id == step_id), None)
    if step is None or step.step_id in completed_step_ids:
        raise ValueError("selected Step must be an uncompleted Plan step")
    if not set(step.input_step_ids) <= completed_step_ids:
        raise ValueError("selected Step dependencies are not complete")
    return step


def _validate_fact_dependencies(
    step: AnalysisStep,
    fact: AnalysisFact,
    prior_facts: Sequence[AnalysisFact],
) -> None:
    expected_fact_ids = [
        next(item for item in prior_facts if item.step_id == step_id).fact_id
        for step_id in step.input_step_ids
    ]
    if fact.payload.input_fact_ids != expected_fact_ids:
        raise ValueError("Fact input IDs do not equal the validated Step dependencies")


def _server_stop_requested(step: AnalysisStep, fact: AnalysisFact) -> bool:
    condition = step.stop_condition
    if isinstance(condition, StopOnEmpty):
        return fact.payload.processing.returned_rows == 0
    if isinstance(condition, StopOnMetric):
        value = fact.metric(condition.metric_key).value
        operations: dict[str, Callable[[object, object], bool]] = {
            "eq": lambda left, right: left == right,
            "lt": lambda left, right: cast(float, left) < cast(float, right),
            "lte": lambda left, right: cast(float, left) <= cast(float, right),
            "gt": lambda left, right: cast(float, left) > cast(float, right),
            "gte": lambda left, right: cast(float, left) >= cast(float, right),
        }
        return operations[condition.operator](value, condition.target)
    return False


def _public_fact(fact: AnalysisFact) -> dict:
    return {
        "fact_id": fact.fact_id,
        "step_id": fact.step_id,
        "primitive": fact.primitive,
        "result_id": fact.result_id,
        "source_ids": list(fact.source_ids),
        "customer_count": len(fact.customer_ids),
        "evidence_count": len(fact.evidence_ids),
        "metrics": [metric.model_dump(mode="json") for metric in fact.metrics],
        "created_at": fact.created_at.isoformat(),
    }


def _public_note(note) -> dict:
    return {
        "note_id": note.note_id,
        "step_id": note.step_id,
        "objective": note.objective,
        "fact_ids": list(note.fact_ids),
        "source_ids": list(note.source_ids),
        "result_ids": list(note.result_ids),
        "claims": [
            {"claim_id": claim.claim_id, "statement": claim.rendered_text} for claim in note.claims
        ],
        "limitations": list(note.limitations),
        "duration_ms": note.duration_ms,
        "plan_revision": note.plan_revision,
    }


def _safe_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.isidentifier() and len(code) <= 64:
        return code
    if isinstance(error, TimeoutError):
        return "analysis_timeout"
    return "analysis_failed"


__all__ = ["AnalysisLoop", "AnalysisModel", "PrimitiveExecutor"]
