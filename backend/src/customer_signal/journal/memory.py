"""In-memory EventJournal for unit and contract tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from customer_signal.journal.events import CanonicalRunEvent, EventDraft
from customer_signal.journal.journal import (
    InvalidEventBatchError,
    RunAlreadyExistsError,
    SequenceConflictError,
    TerminalRunError,
    UnknownRunError,
    validate_batch,
)


@dataclass(slots=True)
class _JournalRun:
    idempotency_key: str
    correlation_id: UUID
    events: list[CanonicalRunEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryEventJournal:
    """Reference implementation of the EventJournal contract."""

    def __init__(self) -> None:
        self._runs: dict[UUID, _JournalRun] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        run_id: UUID,
        first: EventDraft,
        idempotency_key: str,
    ) -> CanonicalRunEvent:
        if first.kind != "run.opened":
            raise InvalidEventBatchError("the create() event must be run.opened")
        if not idempotency_key.strip():
            raise InvalidEventBatchError("idempotency_key must be nonblank")
        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                if existing.idempotency_key != idempotency_key:
                    raise RunAlreadyExistsError("Run exists with a different idempotency key")
                return existing.events[0].model_copy(deep=True)
            event_id = uuid4()
            event = CanonicalRunEvent(
                event_id=event_id,
                run_id=run_id,
                sequence=1,
                occurred_at=_utc_now(),
                pack=first.pack,
                kind=first.kind,
                artifact=first.artifact,
                payload=dict(first.payload),
                causation_id=event_id,
                correlation_id=run_id,
            )
            run = _JournalRun(idempotency_key=idempotency_key, correlation_id=run_id)
            run.events.append(event)
            self._runs[run_id] = run
        async with run.condition:
            run.condition.notify_all()
        return event.model_copy(deep=True)

    async def append(
        self,
        run_id: UUID,
        expected_sequence: int,
        drafts: Sequence[EventDraft],
    ) -> tuple[CanonicalRunEvent, ...]:
        validate_batch(drafts)
        run = self._require(run_id)
        async with run.condition:
            latest = run.events[-1]
            if latest.is_terminal:
                raise TerminalRunError("Run already committed a terminal event")
            if latest.sequence != expected_sequence:
                raise SequenceConflictError(
                    "append expected sequence does not match the committed sequence",
                    latest_sequence=latest.sequence,
                )
            committed: list[CanonicalRunEvent] = []
            causation_id = latest.event_id
            for offset, draft in enumerate(drafts, start=1):
                event = CanonicalRunEvent(
                    event_id=uuid4(),
                    run_id=run_id,
                    sequence=expected_sequence + offset,
                    occurred_at=_utc_now(),
                    pack=draft.pack,
                    kind=draft.kind,
                    artifact=draft.artifact,
                    payload=dict(draft.payload),
                    causation_id=causation_id,
                    correlation_id=run.correlation_id,
                )
                causation_id = event.event_id
                committed.append(event)
            run.events.extend(committed)
            run.condition.notify_all()
        return tuple(event.model_copy(deep=True) for event in committed)

    async def last_sequence(self, run_id: UUID) -> int:
        return self._require(run_id).events[-1].sequence

    def read(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[CanonicalRunEvent]:
        run = self._require(run_id)

        async def iterate() -> AsyncIterator[CanonicalRunEvent]:
            for event in list(run.events):
                if event.sequence > after_sequence:
                    yield event.model_copy(deep=True)

        return iterate()

    def tail(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[CanonicalRunEvent]:
        run = self._require(run_id)

        async def iterate() -> AsyncIterator[CanonicalRunEvent]:
            cursor = after_sequence
            while True:
                async with run.condition:
                    available = [
                        event for event in run.events if event.sequence > cursor
                    ]
                    if not available:
                        if run.events and run.events[-1].is_terminal:
                            return
                        await run.condition.wait()
                        continue
                for event in available:
                    cursor = event.sequence
                    yield event.model_copy(deep=True)
                    if event.is_terminal:
                        return

        return iterate()

    async def list_run_ids(self) -> tuple[UUID, ...]:
        return tuple(self._runs)

    def _require(self, run_id: UUID) -> _JournalRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise UnknownRunError("Run not found in journal") from error


__all__ = ["InMemoryEventJournal"]
