"""EventJournal interface: atomic append, cursor read, and live tail."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol
from uuid import UUID

from customer_signal.journal.events import CanonicalRunEvent, EventDraft


class EventJournalError(RuntimeError):
    """Base error for journal operations."""


class UnknownRunError(EventJournalError, LookupError):
    """Raised when a Run has never been created in this journal."""


class RunAlreadyExistsError(EventJournalError):
    """Raised when create() reuses a run_id with a different idempotency key."""


class SequenceConflictError(EventJournalError):
    """Raised when append() loses a compare-and-swap on the expected sequence."""

    def __init__(self, message: str, *, latest_sequence: int) -> None:
        super().__init__(message)
        self.latest_sequence = latest_sequence


class TerminalRunError(EventJournalError):
    """Raised when append() targets a Run that already committed a terminal event."""


class InvalidEventBatchError(EventJournalError, ValueError):
    """Raised before commit when a draft batch violates journal invariants."""


class EventJournal(Protocol):
    """Append-only Canonical Run Event storage with cursor replay.

    Invariants every implementation must keep:

    - Run-local ``sequence`` values are contiguous ``1..N``.
    - ``create()`` commits the single ``run.opened`` event with sequence 1 and
      returns the identical stored event for the same ``idempotency_key``.
    - ``append()`` is an atomic compare-and-swap batch: either every draft
      commits in order or none does.
    - Exactly one terminal event ends a Run; later appends are rejected.
    - ``read()`` and ``tail()`` deliver committed events only, in sequence
      order, starting strictly after ``after_sequence``.
    - ``tail()`` keeps waiting for new events and finishes after yielding the
      terminal event.
    """

    async def create(
        self,
        run_id: UUID,
        first: EventDraft,
        idempotency_key: str,
    ) -> CanonicalRunEvent: ...

    async def append(
        self,
        run_id: UUID,
        expected_sequence: int,
        drafts: Sequence[EventDraft],
    ) -> tuple[CanonicalRunEvent, ...]: ...

    async def last_sequence(self, run_id: UUID) -> int: ...

    def read(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[CanonicalRunEvent]: ...

    def tail(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[CanonicalRunEvent]: ...

    async def list_run_ids(self) -> tuple[UUID, ...]: ...


def validate_batch(drafts: Sequence[EventDraft]) -> None:
    """Reject a draft batch that could never satisfy journal invariants."""

    if not drafts:
        raise InvalidEventBatchError("append batch must contain at least one draft")
    for draft in drafts:
        if draft.kind == "run.opened":
            raise InvalidEventBatchError("run.opened is only valid as the create() event")
    terminal_positions = [
        index for index, draft in enumerate(drafts) if draft.kind in _TERMINAL
    ]
    if terminal_positions and terminal_positions != [len(drafts) - 1]:
        raise InvalidEventBatchError("a terminal event must be the final draft of its batch")


_TERMINAL = frozenset({"run.completed", "run.degraded", "run.failed"})


__all__ = [
    "EventJournal",
    "EventJournalError",
    "InvalidEventBatchError",
    "RunAlreadyExistsError",
    "SequenceConflictError",
    "TerminalRunError",
    "UnknownRunError",
    "validate_batch",
]
