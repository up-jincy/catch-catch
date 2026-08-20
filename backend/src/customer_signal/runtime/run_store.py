"""Single-process Run state with replayable public events and Artifact restore."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_serializer,
)

from customer_signal.agent.contracts import GenericRunnerOutcome, RunRequest, RunnerOutcome
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNote,
    AnalysisPlan,
    PublicRunError,
    RunStatus,
)
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.reports import GenericOrLegacyReport
from customer_signal.runtime.artifacts import ClarificationRecord, RunArtifact
from customer_signal.runtime.events import (
    GenericRunnerEventType,
    RunnerEventType,
    validate_generic_event,
)


type RunKind = Literal["legacy", "generic"]
type RequestedAgentMode = Literal["auto", "fixture", "gemini"]
type StoredRunEventType = RunnerEventType | GenericRunnerEventType | Literal["done"]

_REPORT_ADAPTER = TypeAdapter(GenericOrLegacyReport)


class RunStoreError(RuntimeError):
    """Base error for public Run-store operations."""


class UnknownRunError(RunStoreError):
    """Raised when a Run identifier is absent."""


class InvalidLastEventIdError(RunStoreError, ValueError):
    """Raised when an SSE replay cursor cannot name an event boundary."""


class InvalidRunTransitionError(RunStoreError):
    """Raised when a lifecycle transition violates the Run state machine."""


class RunError(BaseModel):
    """Legacy client-safe terminal error kept byte-compatible."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    message: str


class StoredRunEvent(BaseModel):
    """One immutable public event in a Run-local contiguous sequence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: int = Field(ge=1)
    type: StoredRunEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime


class RunSnapshot(BaseModel):
    """Public state for either the compatibility or generic Run path."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str
    run_kind: RunKind
    status: RunStatus
    request: RunRequest
    created_at: AwareDatetime
    updated_at: AwareDatetime
    agent_mode: Literal["fixture", "gemini"] | None = None
    goal: AnalysisGoal | None = None
    clarification: ClarificationRecord | None = None
    plan: AnalysisPlan | None = None
    facts: list[AnalysisFact] = Field(default_factory=list)
    notes: list[AnalysisNote] = Field(default_factory=list)
    report: GenericOrLegacyReport | None = None
    limitations: list[str] = Field(default_factory=list)
    error: RunError | PublicRunError | None = None
    failed_step_id: str | None = None
    last_event_id: int = Field(default=0, ge=0)

    @model_serializer(mode="wrap")
    def keep_legacy_json_shape(self, handler):
        data = handler(self)
        if self.run_kind == "legacy":
            for key in (
                "run_kind",
                "goal",
                "clarification",
                "plan",
                "facts",
                "notes",
                "limitations",
                "failed_step_id",
                "last_event_id",
            ):
                data.pop(key, None)
        return data


@dataclass(slots=True)
class _RunState:
    run_id: str
    request: RunRequest
    run_kind: RunKind
    requested_mode: RequestedAgentMode
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    events: list[StoredRunEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    outcome: RunnerOutcome | None = None
    error: RunError | PublicRunError | None = None
    goal: AnalysisGoal | None = None
    clarification: ClarificationRecord | None = None
    plan: AnalysisPlan | None = None
    facts: list[AnalysisFact] = field(default_factory=list)
    notes: list[AnalysisNote] = field(default_factory=list)
    report: GenericOrLegacyReport | None = None
    limitations: list[str] = field(default_factory=list)
    failed_step_id: str | None = None
    agent_mode: Literal["fixture", "gemini"] | None = None
    event_id_offset: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStore:
    """Keep active Run state while allowing persisted terminal history to reload."""

    def __init__(self, artifacts: Iterable[RunArtifact] = ()) -> None:
        self._runs: dict[str, _RunState] = {}
        for artifact in artifacts:
            self.restore_artifact(artifact)

    def create_run(
        self,
        request: RunRequest,
        *,
        run_kind: RunKind = "legacy",
        requested_mode: RequestedAgentMode = "fixture",
    ) -> RunSnapshot:
        now = _utc_now()
        run_id = str(uuid4())
        self._runs[run_id] = _RunState(
            run_id=run_id,
            request=request.model_copy(deep=True),
            run_kind=run_kind,
            requested_mode=requested_mode,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        return self.get_snapshot(run_id)

    def restore_artifact(self, artifact: RunArtifact) -> RunSnapshot:
        run_id = str(artifact.run_id)
        if run_id in self._runs:
            raise ValueError("Run is already registered")
        run_kind: RunKind = (
            "generic"
            if artifact.versions.prompt_version == "generic-v1"
            or artifact.goal is not None
            or artifact.facts
            or artifact.notes
            else "legacy"
        )
        restored_error = artifact.error.model_copy(deep=True) if artifact.error else None
        if run_kind == "legacy" and restored_error is not None:
            restored_error = RunError(
                code=restored_error.code,
                message=restored_error.message,
            )
        restored_mode = artifact.versions.model_version
        agent_mode = (
            "gemini"
            if restored_mode is not None and restored_mode.startswith("gemini")
            else "fixture"
            if restored_mode == "fixture"
            else None
        )
        self._runs[run_id] = _RunState(
            run_id=run_id,
            request=artifact.request.model_copy(deep=True),
            run_kind=run_kind,
            requested_mode="gemini" if agent_mode == "gemini" else "fixture",
            status=artifact.status,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
            error=restored_error,
            goal=artifact.goal.model_copy(deep=True) if artifact.goal else None,
            clarification=(
                artifact.clarification.model_copy(deep=True) if artifact.clarification else None
            ),
            plan=artifact.plan.model_copy(deep=True) if artifact.plan else None,
            facts=[fact.model_copy(deep=True) for fact in artifact.facts],
            notes=[note.model_copy(deep=True) for note in artifact.notes],
            report=artifact.report.model_copy(deep=True) if artifact.report else None,
            limitations=list(artifact.limitations),
            failed_step_id=artifact.failed_step_id,
            agent_mode=agent_mode,
            event_id_offset=artifact.last_event_id,
        )
        return self.get_snapshot(run_id)

    def get_snapshot(self, run_id: str) -> RunSnapshot:
        return self._snapshot(self._require(run_id))

    def get_outcome(self, run_id: str) -> RunnerOutcome | None:
        outcome = self._require(run_id).outcome
        return outcome.model_copy(deep=True) if outcome is not None else None

    def get_run_kind(self, run_id: str) -> RunKind:
        return self._require(run_id).run_kind

    def get_requested_mode(self, run_id: str) -> RequestedAgentMode:
        return self._require(run_id).requested_mode

    def validate_last_event_id(self, run_id: str, last_event_id: int) -> None:
        state = self._require(run_id)
        if (
            isinstance(last_event_id, bool)
            or not isinstance(last_event_id, int)
            or last_event_id < 0
            or last_event_id > state.event_id_offset + len(state.events)
        ):
            raise InvalidLastEventIdError("Last-Event-ID must name an emitted event or zero")

    async def mark_running(self, run_id: str) -> RunSnapshot:
        state = self._require(run_id)
        async with state.condition:
            if state.status not in {"queued", "awaiting_clarification"}:
                raise InvalidRunTransitionError("only queued or awaiting Runs can start")
            state.status = "running"
            state.updated_at = _utc_now()
            state.condition.notify_all()
        return self._snapshot(state)

    async def append_event(
        self,
        run_id: str,
        event_type: StoredRunEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> StoredRunEvent:
        state = self._require(run_id)
        async with state.condition:
            event = StoredRunEvent(
                id=state.event_id_offset + len(state.events) + 1,
                type=event_type,
                payload=payload or {},
                created_at=_utc_now(),
            )
            state.events.append(event)
            state.updated_at = event.created_at
            state.condition.notify_all()
        return event.model_copy(deep=True)

    async def append_generic_event(
        self,
        run_id: str,
        event_type: GenericRunnerEventType,
        payload: dict[str, JsonValue],
    ) -> StoredRunEvent:
        state = self._require(run_id)
        if state.run_kind != "generic":
            raise InvalidRunTransitionError("generic events require a generic Run")
        canonical = validate_generic_event(event_type, payload)
        event = await self.append_event(run_id, event_type, canonical)
        self._apply_generic_event(state, event)
        return event

    async def mark_awaiting(
        self,
        run_id: str,
        outcome: GenericRunnerOutcome,
    ) -> RunSnapshot:
        state = self._require(run_id)
        async with state.condition:
            if state.status != "running" or outcome.status != "awaiting_clarification":
                raise InvalidRunTransitionError("only a running generic Run can await input")
            state.status = "awaiting_clarification"
            state.outcome = outcome.model_copy(deep=True)
            state.agent_mode = outcome.agent_mode
            state.updated_at = _utc_now()
            state.condition.notify_all()
        return self._snapshot(state)

    async def answer_clarification(self, run_id: str, answer: str) -> RunSnapshot:
        state = self._require(run_id)
        normalized = answer.strip()
        if not normalized:
            raise ValueError("clarification answer must be nonblank")
        async with state.condition:
            if state.status != "awaiting_clarification" or state.clarification is None:
                raise InvalidRunTransitionError("Run is not awaiting clarification")
            now = _utc_now()
            state.clarification = state.clarification.model_copy(
                update={"answer": normalized, "answered_at": now}
            )
            state.status = "running"
            state.outcome = None
            state.updated_at = now
            state.condition.notify_all()
        return self._snapshot(state)

    async def mark_generic_terminal(
        self,
        run_id: str,
        outcome: GenericRunnerOutcome,
    ) -> RunSnapshot:
        state = self._require(run_id)
        if outcome.status not in {"completed", "degraded", "failed"}:
            raise InvalidRunTransitionError("generic outcome must be terminal")
        async with state.condition:
            if state.status != "running":
                raise InvalidRunTransitionError("only running Runs can terminate")
            state.status = outcome.status
            state.outcome = outcome.model_copy(deep=True)
            state.goal = outcome.goal.model_copy(deep=True) if outcome.goal else state.goal
            state.plan = outcome.plan.model_copy(deep=True) if outcome.plan else state.plan
            state.facts = [fact.model_copy(deep=True) for fact in outcome.facts]
            state.notes = [note.model_copy(deep=True) for note in outcome.notes]
            state.report = outcome.report.model_copy(deep=True) if outcome.report else None
            state.limitations = list(outcome.limitations)
            state.error = outcome.error.model_copy(deep=True) if outcome.error else None
            state.failed_step_id = outcome.failed_step_id
            state.agent_mode = outcome.agent_mode
            state.updated_at = _utc_now()
            state.condition.notify_all()
        return self._snapshot(state)

    async def mark_completed(self, run_id: str, outcome: RunnerOutcome) -> RunSnapshot:
        if isinstance(outcome, GenericRunnerOutcome):
            return await self.mark_generic_terminal(run_id, outcome)
        state = self._require(run_id)
        async with state.condition:
            if state.status != "running":
                raise InvalidRunTransitionError("only running Runs can complete")
            state.status = "completed"
            state.outcome = outcome.model_copy(deep=True)
            state.report = outcome.report.model_copy(deep=True)
            state.agent_mode = outcome.agent_mode
            state.error = None
            state.updated_at = _utc_now()
            state.condition.notify_all()
        return self._snapshot(state)

    async def mark_failed(
        self,
        run_id: str,
        error: RunError | PublicRunError,
    ) -> RunSnapshot:
        state = self._require(run_id)
        async with state.condition:
            if state.status not in {"queued", "running", "awaiting_clarification"}:
                raise InvalidRunTransitionError("only nonterminal Runs can fail")
            state.status = "failed"
            state.outcome = None
            state.error = error.model_copy(deep=True)
            state.updated_at = _utc_now()
            state.condition.notify_all()
        return self._snapshot(state)

    def stream_events(
        self,
        run_id: str,
        last_event_id: int = 0,
    ) -> AsyncIterator[StoredRunEvent]:
        state = self._require(run_id)
        self.validate_last_event_id(run_id, last_event_id)

        async def iterate() -> AsyncIterator[StoredRunEvent]:
            cursor = last_event_id
            while True:
                async with state.condition:
                    available = state.events[max(cursor - state.event_id_offset, 0) :]
                    if not available:
                        if state.status == "awaiting_clarification":
                            return
                        if state.events and state.events[-1].type == "done":
                            return
                        if state.status in {"completed", "degraded", "failed"}:
                            return
                        await state.condition.wait()
                        continue

                for event in available:
                    cursor = event.id
                    yield event.model_copy(deep=True)
                    if event.type == "done":
                        return

        return iterate()

    def _apply_generic_event(self, state: _RunState, event: StoredRunEvent) -> None:
        payload = event.payload
        if event.type == "goal_created":
            state.goal = AnalysisGoal.model_validate_json(_json(payload["goal"]))
        elif event.type in {"plan_created", "plan_revised"}:
            state.plan = AnalysisPlan.model_validate_json(_json(payload["plan"]))
        elif event.type == "fact_created":
            state.facts = [
                *state.facts,
                AnalysisFact.model_validate_json(_json(payload["fact"])),
            ]
        elif event.type == "analysis_note_created":
            state.notes = [
                *state.notes,
                AnalysisNote.model_validate_json(_json(payload["note"])),
            ]
        elif event.type == "clarification_required":
            state.clarification = ClarificationRecord(
                clarification_id=str(payload["clarification_id"]),
                question=str(payload["question"]),
                requested_at=event.created_at,
            )
        elif event.type == "result":
            state.agent_mode = str(payload["agent_mode"])
            state.report = _REPORT_ADAPTER.validate_json(_json(payload["report"]))

    def _require(self, run_id: str) -> _RunState:
        try:
            UUID(run_id)
            return self._runs[run_id]
        except (KeyError, ValueError, AttributeError) as error:
            raise UnknownRunError("Run not found") from error

    @staticmethod
    def _snapshot(state: _RunState) -> RunSnapshot:
        return RunSnapshot(
            run_id=state.run_id,
            run_kind=state.run_kind,
            status=state.status,
            request=state.request.model_copy(deep=True),
            created_at=state.created_at,
            updated_at=state.updated_at,
            agent_mode=state.agent_mode,
            goal=state.goal.model_copy(deep=True) if state.goal else None,
            clarification=(
                state.clarification.model_copy(deep=True) if state.clarification else None
            ),
            plan=state.plan.model_copy(deep=True) if state.plan else None,
            facts=[fact.model_copy(deep=True) for fact in state.facts],
            notes=[note.model_copy(deep=True) for note in state.notes],
            report=state.report.model_copy(deep=True) if state.report else None,
            limitations=list(state.limitations),
            error=state.error.model_copy(deep=True) if state.error else None,
            failed_step_id=state.failed_step_id,
            last_event_id=state.event_id_offset + len(state.events),
        )


def _json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "InvalidLastEventIdError",
    "InvalidRunTransitionError",
    "RequestedAgentMode",
    "RunError",
    "RunKind",
    "RunSnapshot",
    "RunStatus",
    "RunStore",
    "RunStoreError",
    "StoredRunEvent",
    "StoredRunEventType",
    "UnknownRunError",
]
