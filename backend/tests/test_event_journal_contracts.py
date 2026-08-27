"""One EventJournal contract suite applied to the memory and SQLite adapters."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from customer_signal.journal.events import EventDraft, PackRef, VersionedValue
from customer_signal.journal.journal import (
    InvalidEventBatchError,
    RunAlreadyExistsError,
    SequenceConflictError,
    TerminalRunError,
    UnknownRunError,
)
from customer_signal.journal.memory import InMemoryEventJournal
from customer_signal.journal.sqlite import SQLiteEventJournal

PACK = PackRef(
    pack_id="customer_signal",
    pack_version="1.0.0",
    contract_digest="digest-0001",
)


def opened() -> EventDraft:
    return EventDraft(kind="run.opened", pack=PACK, payload={"status": "running"})


def artifact_committed(kind_id: str = "goal") -> EventDraft:
    return EventDraft(
        kind="artifact.committed",
        pack=PACK,
        artifact=VersionedValue(
            schema_id=f"customer_signal.{kind_id}.v1",
            schema_digest="digest-0002",
            value={"title": "요금제 변경 반복 문의"},
        ),
    )


def activity(status: str = "started") -> EventDraft:
    return EventDraft(kind="activity.changed", pack=PACK, payload={"status": status})


def completed() -> EventDraft:
    return EventDraft(kind="run.completed", pack=PACK, payload={"status": "completed"})


@pytest.fixture(params=["memory", "sqlite"])
def journal(request, tmp_path: Path):
    if request.param == "memory":
        yield InMemoryEventJournal()
        return
    store = SQLiteEventJournal(tmp_path / "journal.sqlite3")
    yield store


async def test_create_commits_run_opened_with_sequence_one(journal) -> None:
    run_id = uuid4()
    event = await journal.create(run_id, opened(), idempotency_key="cmd-1")
    assert event.sequence == 1
    assert event.kind == "run.opened"
    assert event.run_id == run_id
    assert event.causation_id == event.event_id
    assert await journal.last_sequence(run_id) == 1


async def test_create_is_idempotent_for_the_same_key(journal) -> None:
    run_id = uuid4()
    first = await journal.create(run_id, opened(), idempotency_key="cmd-1")
    replay = await journal.create(run_id, opened(), idempotency_key="cmd-1")
    assert replay == first
    with pytest.raises(RunAlreadyExistsError):
        await journal.create(run_id, opened(), idempotency_key="cmd-2")


async def test_create_rejects_non_opened_first_event(journal) -> None:
    with pytest.raises(InvalidEventBatchError):
        await journal.create(uuid4(), activity(), idempotency_key="cmd-1")


async def test_append_extends_a_contiguous_sequence(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    events = await journal.append(run_id, 1, [artifact_committed(), activity()])
    assert [event.sequence for event in events] == [2, 3]
    assert events[0].causation_id != events[1].causation_id
    assert all(event.correlation_id == run_id for event in events)
    stored = [event async for event in journal.read(run_id)]
    assert [event.sequence for event in stored] == [1, 2, 3]


async def test_append_conflicts_on_stale_expected_sequence(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    await journal.append(run_id, 1, [activity()])
    with pytest.raises(SequenceConflictError) as conflict:
        await journal.append(run_id, 1, [activity("completed")])
    assert conflict.value.latest_sequence == 2
    assert await journal.last_sequence(run_id) == 2


async def test_append_batch_is_atomic(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    with pytest.raises(InvalidEventBatchError):
        await journal.append(run_id, 1, [completed(), activity()])
    assert await journal.last_sequence(run_id) == 1


async def test_append_rejects_terminal_followups(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    await journal.append(run_id, 1, [completed()])
    with pytest.raises(TerminalRunError):
        await journal.append(run_id, 2, [activity()])


async def test_read_starts_strictly_after_the_cursor(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    await journal.append(run_id, 1, [artifact_committed(), activity(), completed()])
    replayed = [event async for event in journal.read(run_id, after_sequence=2)]
    assert [event.sequence for event in replayed] == [3, 4]


async def test_unknown_run_is_reported(journal) -> None:
    with pytest.raises(UnknownRunError):
        await journal.last_sequence(uuid4())
    with pytest.raises(UnknownRunError):
        [event async for event in journal.read(uuid4())]


async def test_tail_delivers_live_events_and_ends_at_terminal(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")

    async def consume() -> list[int]:
        return [event.sequence async for event in journal.tail(run_id)]

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await journal.append(run_id, 1, [artifact_committed()])
    await journal.append(run_id, 2, [completed()])
    sequences = await asyncio.wait_for(consumer, timeout=5)
    assert sequences == [1, 2, 3]


async def test_tail_resumes_from_cursor_after_disconnect(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    await journal.append(run_id, 1, [artifact_committed(), activity()])

    first_pass = journal.tail(run_id)
    seen = []
    async for event in first_pass:
        seen.append(event.sequence)
        if event.sequence == 2:
            break
    await first_pass.aclose()

    await journal.append(run_id, 3, [completed()])
    resumed = [
        event.sequence async for event in journal.tail(run_id, after_sequence=seen[-1])
    ]
    assert resumed == [3, 4]


async def test_forbidden_payload_keys_never_commit(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    with pytest.raises(ValueError):
        EventDraft(kind="activity.changed", pack=PACK, payload={"prompt": "hidden"})
    assert await journal.last_sequence(run_id) == 1


async def test_artifact_commit_requires_versioned_value(journal) -> None:
    with pytest.raises(ValueError):
        EventDraft(kind="artifact.committed", pack=PACK)
    with pytest.raises(ValueError):
        EventDraft(
            kind="activity.changed",
            pack=PACK,
            artifact=VersionedValue(
                schema_id="customer_signal.goal.v1",
                schema_digest="digest-0002",
                value={},
            ),
        )


async def test_tail_returns_promptly_when_cursor_is_at_or_past_terminal(journal) -> None:
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    await journal.append(run_id, 1, [completed()])
    drained = await asyncio.wait_for(
        _collect(journal.tail(run_id, after_sequence=2)), timeout=2
    )
    assert drained == []


async def _collect(iterator) -> list[int]:
    return [event.sequence async for event in iterator]


async def test_sqlite_append_stays_atomic_when_the_caller_is_cancelled(
    tmp_path: Path,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "cancel.sqlite3")
    run_id = uuid4()
    await journal.create(run_id, opened(), idempotency_key="cmd-1")
    batch = [activity() for _ in range(200)]

    task = asyncio.create_task(journal.append(run_id, 1, batch))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    last = await journal.last_sequence(run_id)
    assert last in {1, 201}
    events = [event async for event in journal.read(run_id)]
    assert [event.sequence for event in events] == list(range(1, last + 1))
    terminal = await journal.append(run_id, last, [completed()])
    assert terminal[-1].sequence == last + 1
    await journal.close()


async def test_sqlite_replays_identically_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.sqlite3"
    first = SQLiteEventJournal(path)
    run_id = uuid4()
    await first.create(run_id, opened(), idempotency_key="cmd-1")
    await first.append(run_id, 1, [artifact_committed(), activity(), completed()])
    before = [event async for event in first.read(run_id)]
    await first.close()

    reopened = SQLiteEventJournal(path)
    after = [event async for event in reopened.read(run_id)]
    assert after == before
    assert await reopened.list_run_ids() == (run_id,)
    with pytest.raises(TerminalRunError):
        await reopened.append(run_id, 4, [activity()])
    await reopened.close()
