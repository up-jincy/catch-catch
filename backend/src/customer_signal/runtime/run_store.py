"""Single-process run state with replayable, run-scoped public events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from customer_signal.agent.contracts import RunRequest, RunnerOutcome
from customer_signal.domain.reports import InsightReport
from customer_signal.runtime.events import RunnerEventType


type RunStatus = Literal["queued", "running", "completed", "failed"]
type StoredRunEventType = RunnerEventType | Literal["done"]


class RunStoreError(RuntimeError):
    """Base error for public run-store operations."""


class UnknownRunError(RunStoreError):
    """Raised when a run identifier is absent from this process."""


class InvalidLastEventIdError(RunStoreError, ValueError):
    """Raised when an SSE replay cursor cannot name an emitted event boundary."""


class InvalidRunTransitionError(RunStoreError):
    """Raised when a lifecycle transition violates the run state machine."""


class RunError(BaseModel):
    """A small client-safe terminal error."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    message: str


class StoredRunEvent(BaseModel):
    """One immutable public event in a run-local sequence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: int = Field(ge=1)
    type: StoredRunEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime


class RunSnapshot(BaseModel):
    """Public lifecycle state; private runner facts never cross this boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str
    status: RunStatus
    request: RunRequest
    created_at: AwareDatetime
    updated_at: AwareDatetime
    agent_mode: Literal["fixture", "gemini"] | None = None
    report: InsightReport | None = None
    error: RunError | None = None


@dataclass(slots=True)
class _RunState:
    run_id: str
    request: RunRequest
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    events: list[StoredRunEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    outcome: RunnerOutcome | None = None
    error: RunError | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStore:
    """Keep bounded demo run state and SSE events in this server process."""

    def __init__(self) -> None:
        self._runs: dict[str, _RunState] = {}

    def create_run(self, request: RunRequest) -> RunSnapshot:
        now = _utc_now()
        run_id = str(uuid4())
        self._runs[run_id] = _RunState(
            run_id=run_id,
            request=request.model_copy(deep=True),
            status="queued",
            created_at=now,
            updated_at=now,
        )
        return self.get_snapshot(run_id)

    def get_snapshot(self, run_id: str) -> RunSnapshot:
        return self._snapshot(self._require(run_id))

    def get_outcome(self, run_id: str) -> RunnerOutcome | None:
        outcome = self._require(run_id).outcome
        return outcome.model_copy(deep=True) if outcome is not None else None

    def validate_last_event_id(self, run_id: str, last_event_id: int) -> None:
        state = self._require(run_id)
        if (
            isinstance(last_event_id, bool)
            or not isinstance(last_event_id, int)
            or last_event_id < 0
            or last_event_id > len(state.events)
        ):
            raise InvalidLastEventIdError("Last-Event-ID must name an emitted event or zero")

    async def mark_running(self, run_id: str) -> RunSnapshot:
        state = self._require(run_id)
        async with state.condition:
            if state.status != "queued":
                raise InvalidRunTransitionError("only queued runs can start")
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
                id=len(state.events) + 1,
                type=event_type,
                payload=payload or {},
                created_at=_utc_now(),
            )
            state.events.append(event)
            state.updated_at = event.created_at
            state.condition.notify_all()
        return event.model_copy(deep=True)

    async def mark_completed(self, run_id: str, outcome: RunnerOutcome) -> RunSnapshot:
        state = self._require(run_id)
        async with state.condition:
            if state.status != "running":
                raise InvalidRunTransitionError("only running runs can complete")
            state.status = "completed"
            state.outcome = outcome.model_copy(deep=True)
            state.error = None
            state.updated_at = _utc_now()
            state.condition.notify_all()
        return self._snapshot(state)

    async def mark_failed(self, run_id: str, error: RunError) -> RunSnapshot:
        state = self._require(run_id)
        async with state.condition:
            if state.status not in {"queued", "running"}:
                raise InvalidRunTransitionError("only nonterminal runs can fail")
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
                    available = state.events[cursor:]
                    if not available:
                        if state.events and state.events[-1].type == "done":
                            return
                        await state.condition.wait()
                        continue

                for event in available:
                    cursor = event.id
                    yield event.model_copy(deep=True)
                    if event.type == "done":
                        return

        return iterate()

    def _require(self, run_id: str) -> _RunState:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise UnknownRunError("run not found") from error

    @staticmethod
    def _snapshot(state: _RunState) -> RunSnapshot:
        outcome = state.outcome
        return RunSnapshot(
            run_id=state.run_id,
            status=state.status,
            request=state.request.model_copy(deep=True),
            created_at=state.created_at,
            updated_at=state.updated_at,
            agent_mode=outcome.agent_mode if outcome is not None else None,
            report=outcome.report.model_copy(deep=True) if outcome is not None else None,
            error=state.error.model_copy(deep=True) if state.error is not None else None,
        )


__all__ = [
    "InvalidLastEventIdError",
    "InvalidRunTransitionError",
    "RunError",
    "RunSnapshot",
    "RunStatus",
    "RunStore",
    "RunStoreError",
    "StoredRunEvent",
    "StoredRunEventType",
    "UnknownRunError",
]
