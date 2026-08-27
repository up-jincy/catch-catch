"""SQLite EventJournal: durable Canonical Run Events that survive restarts."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from customer_signal.journal.events import (
    TERMINAL_EVENT_KINDS,
    CanonicalRunEvent,
    EventDraft,
)
from customer_signal.journal.journal import (
    InvalidEventBatchError,
    RunAlreadyExistsError,
    SequenceConflictError,
    TerminalRunError,
    UnknownRunError,
    validate_batch,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_runs (
    run_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_events (
    run_id TEXT NOT NULL REFERENCES journal_runs (run_id),
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
"""

_TAIL_POLL_SECONDS = 0.25


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteEventJournal:
    """Local durable journal for the hackathon runtime.

    Writes serialize on one connection guarded by a lock; blocking SQLite work
    runs in a thread so the event loop never stalls.  Live tails wake through
    an in-process condition and fall back to short polling so events appended
    by another process are still observed.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)

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
            event = await asyncio.to_thread(
                self._create_sync, run_id, first, idempotency_key
            )
        await self._notify()
        return event

    async def append(
        self,
        run_id: UUID,
        expected_sequence: int,
        drafts: Sequence[EventDraft],
    ) -> tuple[CanonicalRunEvent, ...]:
        validate_batch(drafts)
        async with self._lock:
            committed = await asyncio.to_thread(
                self._append_sync, run_id, expected_sequence, list(drafts)
            )
        await self._notify()
        return committed

    async def last_sequence(self, run_id: UUID) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._last_sequence_sync, run_id)

    def read(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[CanonicalRunEvent]:
        async def iterate() -> AsyncIterator[CanonicalRunEvent]:
            async with self._lock:
                rows = await asyncio.to_thread(
                    self._rows_after_sync, run_id, after_sequence, True
                )
            for body in rows:
                yield CanonicalRunEvent.model_validate_json(body)

        return iterate()

    def tail(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[CanonicalRunEvent]:
        async def iterate() -> AsyncIterator[CanonicalRunEvent]:
            cursor = after_sequence
            require_known = True
            while True:
                async with self._lock:
                    rows = await asyncio.to_thread(
                        self._rows_after_sync, run_id, cursor, require_known
                    )
                require_known = False
                if rows:
                    for body in rows:
                        event = CanonicalRunEvent.model_validate_json(body)
                        cursor = event.sequence
                        yield event
                        if event.is_terminal:
                            return
                    continue
                async with self._changed:
                    try:
                        await asyncio.wait_for(
                            self._changed.wait(), timeout=_TAIL_POLL_SECONDS
                        )
                    except TimeoutError:
                        pass

        return iterate()

    async def list_run_ids(self) -> tuple[UUID, ...]:
        async with self._lock:
            rows = await asyncio.to_thread(self._run_ids_sync)
        return tuple(UUID(value) for value in rows)

    async def _notify(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    def _create_sync(
        self,
        run_id: UUID,
        first: EventDraft,
        idempotency_key: str,
    ) -> CanonicalRunEvent:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT idempotency_key FROM journal_runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            if row is not None:
                if row[0] != idempotency_key:
                    raise RunAlreadyExistsError(
                        "Run exists with a different idempotency key"
                    )
                body = connection.execute(
                    "SELECT body FROM journal_events WHERE run_id = ? AND sequence = 1",
                    (str(run_id),),
                ).fetchone()
                connection.commit()
                return CanonicalRunEvent.model_validate_json(body[0])
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
            connection.execute(
                "INSERT INTO journal_runs (run_id, idempotency_key, correlation_id,"
                " created_at) VALUES (?, ?, ?, ?)",
                (
                    str(run_id),
                    idempotency_key,
                    str(run_id),
                    event.occurred_at.isoformat(),
                ),
            )
            self._insert_event(connection, event)
            connection.commit()
            return event
        except BaseException:
            connection.rollback()
            raise

    def _append_sync(
        self,
        run_id: UUID,
        expected_sequence: int,
        drafts: list[EventDraft],
    ) -> tuple[CanonicalRunEvent, ...]:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            run_row = connection.execute(
                "SELECT correlation_id FROM journal_runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            if run_row is None:
                raise UnknownRunError("Run not found in journal")
            latest = connection.execute(
                "SELECT sequence, event_id, kind FROM journal_events"
                " WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
            latest_sequence, latest_event_id, latest_kind = latest
            if latest_kind in TERMINAL_EVENT_KINDS:
                raise TerminalRunError("Run already committed a terminal event")
            if latest_sequence != expected_sequence:
                raise SequenceConflictError(
                    "append expected sequence does not match the committed sequence",
                    latest_sequence=latest_sequence,
                )
            committed: list[CanonicalRunEvent] = []
            causation_id = UUID(latest_event_id)
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
                    correlation_id=UUID(run_row[0]),
                )
                causation_id = event.event_id
                self._insert_event(connection, event)
                committed.append(event)
            connection.commit()
            return tuple(committed)
        except BaseException:
            connection.rollback()
            raise

    def _last_sequence_sync(self, run_id: UUID) -> int:
        row = self._connection.execute(
            "SELECT MAX(sequence) FROM journal_events WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None or row[0] is None:
            raise UnknownRunError("Run not found in journal")
        return int(row[0])

    def _rows_after_sync(
        self,
        run_id: UUID,
        after_sequence: int,
        require_known: bool,
    ) -> list[str]:
        if require_known:
            known = self._connection.execute(
                "SELECT 1 FROM journal_runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            if known is None:
                raise UnknownRunError("Run not found in journal")
        rows = self._connection.execute(
            "SELECT body FROM journal_events WHERE run_id = ? AND sequence > ?"
            " ORDER BY sequence ASC",
            (str(run_id), after_sequence),
        ).fetchall()
        return [row[0] for row in rows]

    def _run_ids_sync(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT run_id FROM journal_runs ORDER BY created_at ASC"
        ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: CanonicalRunEvent) -> None:
        connection.execute(
            "INSERT INTO journal_events (run_id, sequence, event_id, kind, body)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                str(event.run_id),
                event.sequence,
                str(event.event_id),
                event.kind,
                event.model_dump_json(),
            ),
        )


__all__ = ["SQLiteEventJournal"]
