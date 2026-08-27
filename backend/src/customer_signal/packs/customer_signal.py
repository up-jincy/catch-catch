"""CustomerSignalPack: the existing generic analysis wrapped as one Analysis Pack.

The pack reuses ``AnalysisLoop`` (with its Fixture and Gemini ``AnalysisModel``
adapters) unchanged and translates its public ``AnalysisEvent`` stream into
Pack emissions.  Lifecycle, sequencing, and persistence stay with the Kernel.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal, cast
from uuid import UUID

from pydantic import JsonValue

from customer_signal.agent.analysis_loop import AnalysisLoop
from customer_signal.agent.contracts import AnalysisEvent, GenericRunnerOutcome, RunRequest
from customer_signal.domain.analysis import AnalysisGoal, AnalysisNote, AnalysisPlan
from customer_signal.domain.analysis import PublicRunError
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.reports import CustomerSignalReport
from customer_signal.packs.contracts import (
    ActivityDraft,
    AnalysisPackSpec,
    ArtifactSchema,
    FactDraft,
    GoalDraft,
    InteractionDraft,
    NoteDraft,
    OutcomeDraft,
    PackContext,
    PackDomainError,
    PackEmission,
    PlanDraft,
    ReportDraft,
)

_RUN_FAILED_MESSAGE = "분석 실행에 실패했습니다."

CUSTOMER_SIGNAL_PACK_SPEC = AnalysisPackSpec(
    pack_id="customer_signal",
    pack_version="1.0.0",
    title_ko="고객 신호 분석",
    description_ko=(
        "합성 고객 이벤트에서 Goal, Plan, Fact, Note, Report를 검증하며 만드는 "
        "범용 고객 신호 분석입니다."
    ),
    input_schema_id="customer_signal.run_request.v1",
    artifact_schemas=(
        ArtifactSchema(
            kind="goal", schema_id="customer_signal.goal.v1", model=AnalysisGoal
        ),
        ArtifactSchema(
            kind="plan", schema_id="customer_signal.plan.v1", model=AnalysisPlan
        ),
        ArtifactSchema(
            kind="fact", schema_id="customer_signal.fact.v1", model=AnalysisFact
        ),
        ArtifactSchema(
            kind="note", schema_id="customer_signal.note.v1", model=AnalysisNote
        ),
        ArtifactSchema(
            kind="report",
            schema_id="customer_signal.report.v1",
            model=CustomerSignalReport,
        ),
    ),
    required_catalog_keys=("Card", "Metric", "Table", "Text", "Timeline", "Notice"),
)

_DONE = object()


class CustomerSignalPack:
    """One deep module owning customer-signal analysis behind the Pack seam."""

    Input = RunRequest
    spec = CUSTOMER_SIGNAL_PACK_SPEC

    def __init__(
        self,
        *,
        fixture_loop: AnalysisLoop,
        gemini_loop: AnalysisLoop | None = None,
    ) -> None:
        self._loops: dict[str, AnalysisLoop | None] = {
            "fixture": fixture_loop,
            "gemini": gemini_loop,
        }
        self._outcomes: dict[UUID, GenericRunnerOutcome] = {}

    @property
    def has_gemini(self) -> bool:
        return self._loops["gemini"] is not None

    def take_outcome(self, run_id: UUID) -> GenericRunnerOutcome | None:
        """Hand the loop's terminal outcome to the runtime wire adapter once."""

        return self._outcomes.pop(run_id, None)

    async def execute(
        self,
        request: RunRequest,
        context: PackContext,
    ) -> AsyncIterator[PackEmission]:
        loop = self._select_loop(context)
        queue: asyncio.Queue[AnalysisEvent | object] = asyncio.Queue()
        pending_error: PublicRunError | None = None

        async def pump() -> GenericRunnerOutcome:
            try:
                return await loop.run(request, emit=queue.put)
            finally:
                queue.put_nowait(_DONE)

        task = asyncio.create_task(pump(), name=f"customer-signal-pack-{context.run_id}")
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                event = cast(AnalysisEvent, item)
                if event.type == "error":
                    pending_error = PublicRunError.model_validate(event.payload)
                    continue
                emission = _emission_for(event)
                if emission is not None:
                    yield emission
            try:
                outcome = await task
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise PackDomainError("generic_run_failed", _RUN_FAILED_MESSAGE) from error
            self._outcomes[context.run_id] = outcome
            yield _outcome_draft(outcome, pending_error)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _select_loop(self, context: PackContext) -> AnalysisLoop:
        mode = cast(str, context.options.get("mode", "auto"))
        if mode == "auto":
            mode = "gemini" if self.has_gemini else "fixture"
        loop = self._loops.get(mode)
        if loop is None:
            raise PackDomainError("generic_run_failed", _RUN_FAILED_MESSAGE)
        return loop


def _emission_for(event: AnalysisEvent) -> PackEmission | None:
    payload = event.payload
    if event.type == "goal_created":
        return GoalDraft(value=_object(payload["goal"]))
    if event.type == "plan_created":
        return PlanDraft(value=_object(payload["plan"]))
    if event.type == "plan_revised":
        return PlanDraft(value=_object(payload["plan"]), revised=True)
    if event.type == "step_started":
        return ActivityDraft(
            payload={"activity": "step", "phase": "started", **payload}
        )
    if event.type == "step_completed":
        return ActivityDraft(
            payload={"activity": "step", "phase": "completed", **payload}
        )
    if event.type == "report_validating":
        return ActivityDraft(payload={"activity": "report_validation", **payload})
    if event.type == "fact_created":
        return FactDraft(
            value=_object(payload["fact"]),
            step_id=cast(str, payload["step_id"]),
        )
    if event.type == "analysis_note_created":
        return NoteDraft(value=_object(payload["note"]))
    if event.type == "result":
        return ReportDraft(
            value=_object(payload["report"]),
            meta={"agent_mode": payload["agent_mode"]},
        )
    if event.type == "clarification_required":
        return InteractionDraft(phase="requested", payload=dict(payload))
    return None


def _outcome_draft(
    outcome: GenericRunnerOutcome,
    pending_error: PublicRunError | None,
) -> OutcomeDraft:
    if outcome.status == "awaiting_clarification":
        return OutcomeDraft(status="awaiting_input")
    if outcome.status == "failed":
        error = (
            pending_error
            or outcome.error
            or PublicRunError(code="generic_run_failed", message=_RUN_FAILED_MESSAGE)
        )
        return OutcomeDraft(
            status="failed",
            error=error,
            limitations=list(outcome.limitations),
        )
    status = cast(Literal["completed", "degraded"], outcome.status)
    return OutcomeDraft(status=status, limitations=list(outcome.limitations))


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("artifact payload must be a JSON object")
    return value


__all__ = ["CUSTOMER_SIGNAL_PACK_SPEC", "CustomerSignalPack"]
