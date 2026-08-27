"""AnalysisPackHarness: the contract every Analysis Pack must pass.

New Packs run these checks against an in-memory journal before touching the
composition root.  The harness verifies kernel-observable behavior only, so it
works for any Pack without knowing its domain.
"""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, JsonValue

from customer_signal.journal.events import CanonicalRunEvent
from customer_signal.journal.memory import InMemoryEventJournal
from customer_signal.packs.contracts import AnalysisPackAdapter
from customer_signal.packs.kernel import PackKernel, PackRunResult
from customer_signal.presentation.generic import GenericRunProjector
from customer_signal.presentation.projector import PackProjector, fold_intents

_TERMINAL_KINDS = {"run.completed", "run.degraded", "run.failed"}
_FORBIDDEN_TEXT_TOKENS = (
    '"prompt"',
    '"raw_fields"',
    '"chain_of_thought"',
    '"internal_reasoning"',
    '"provider_response"',
)


class PackContractReport(BaseModel):
    """What the harness observed for one Pack execution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: str
    event_kinds: list[str]
    artifact_schema_ids: list[str]
    intent_count: int


async def assert_pack_contract(
    pack: AnalysisPackAdapter,
    request_data: dict[str, JsonValue],
    *,
    options: dict[str, JsonValue] | None = None,
    projector: PackProjector | None = None,
    timeout_seconds: float = 60.0,
) -> PackContractReport:
    """Execute the Pack twice through the kernel and assert its contract."""

    first_result, first_events = await _run_once(
        pack, request_data, options=options, timeout_seconds=timeout_seconds
    )
    second_result, second_events = await _run_once(
        pack, request_data, options=options, timeout_seconds=timeout_seconds
    )

    _assert_lifecycle(first_result, first_events)
    _assert_lifecycle(second_result, second_events)
    _assert_public_safety(first_events)

    first_shape = _shape(first_events)
    second_shape = _shape(second_events)
    assert first_shape == second_shape, (
        "pack output schema must be deterministic for the same input: "
        f"{first_shape} != {second_shape}"
    )

    resolved_projector = projector or getattr(pack, "projector", None) or GenericRunProjector()
    intents_once = fold_intents(resolved_projector, first_events)
    intents_twice = fold_intents(resolved_projector, first_events)
    assert intents_once == intents_twice, "projector must be pure and idempotent"
    for intent in intents_once:
        json.dumps(intent.model_dump(mode="json"))

    return PackContractReport(
        status=first_result.status,
        event_kinds=[event.kind for event in first_events],
        artifact_schema_ids=[
            event.artifact.schema_id
            for event in first_events
            if event.artifact is not None
        ],
        intent_count=len(intents_once),
    )


async def _run_once(
    pack: AnalysisPackAdapter,
    request_data: dict[str, JsonValue],
    *,
    options: dict[str, JsonValue] | None,
    timeout_seconds: float,
) -> tuple[PackRunResult, list[CanonicalRunEvent]]:
    journal = InMemoryEventJournal()
    kernel = PackKernel(journal, timeout_seconds=timeout_seconds)
    run_id = uuid4()
    result = await kernel.run(pack, dict(request_data), run_id=run_id, options=options)
    events = [event async for event in journal.read(run_id)]
    return result, events


def _assert_lifecycle(result: PackRunResult, events: list[CanonicalRunEvent]) -> None:
    assert events, "a pack run must commit at least run.opened"
    assert events[0].kind == "run.opened", "the first event must be run.opened"
    sequences = [event.sequence for event in events]
    assert sequences == list(range(1, len(events) + 1)), "sequences must be contiguous"
    terminal = [event for event in events if event.kind in _TERMINAL_KINDS]
    if result.status == "awaiting_input":
        assert not terminal, "an awaiting run must not commit a terminal event"
        assert events[-1].kind == "run.awaiting_input"
    else:
        assert len(terminal) == 1, "exactly one terminal event must be committed"
        assert events[-1] is terminal[0], "the terminal event must be last"
        assert events[-1].kind == f"run.{result.status}"


def _assert_public_safety(events: list[CanonicalRunEvent]) -> None:
    for event in events:
        serialized = event.model_dump_json()
        for token in _FORBIDDEN_TEXT_TOKENS:
            assert token not in serialized, (
                f"event {event.sequence} leaks a private key {token}"
            )


def _shape(events: list[CanonicalRunEvent]) -> list[tuple[str, str | None]]:
    return [
        (
            event.kind,
            event.artifact.schema_id if event.artifact is not None else None,
        )
        for event in events
    ]


__all__ = ["PackContractReport", "assert_pack_contract"]
