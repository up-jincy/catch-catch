"""The one adapter between Canonical Run Events and the public generic SSE wire.

Canonical Run Events in the journal are the source of truth.  This module
projects them into the existing ``GenericRunnerEventType`` wire vocabulary so
the current Frontend contract stays byte-compatible, and rebuilds a Run's wire
history after a Backend restart.  Presentation-shape changes happen here, not
in Packs or the Kernel.
"""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue, ValidationError

from customer_signal.agent.contracts import RunRequest
from customer_signal.journal.events import CanonicalRunEvent
from customer_signal.journal.journal import EventJournal
from customer_signal.runtime.events import GenericRunnerEventType, validate_generic_event
from customer_signal.runtime.run_store import RunStore, UnknownRunError

type WireEvent = tuple[GenericRunnerEventType, dict[str, JsonValue]]

_ACTIVITY_MARKER_KEYS = frozenset({"activity", "phase"})


def wire_events_for(event: CanonicalRunEvent) -> list[WireEvent]:
    """Project one committed Canonical Run Event to zero or more wire events."""

    kind = event.kind
    payload = event.payload

    if kind == "run.opened":
        return [("run_started", {"status": "running"})]

    if kind == "artifact.committed":
        assert event.artifact is not None
        value = event.artifact.value
        artifact_kind = payload.get("artifact_kind")
        if artifact_kind == "goal":
            return [("goal_created", {"goal": value})]
        if artifact_kind == "plan":
            wire_type: GenericRunnerEventType = (
                "plan_revised" if payload.get("revised") else "plan_created"
            )
            return [(wire_type, {"plan": value})]
        if artifact_kind == "fact":
            step_id = payload.get("step_id")
            if step_id is None and isinstance(value, dict):
                step_id = value.get("step_id")
            return [("fact_created", {"step_id": step_id, "fact": value})]
        if artifact_kind == "note":
            return [("analysis_note_created", {"note": value})]
        if artifact_kind == "report":
            return [
                (
                    "result",
                    {"agent_mode": payload.get("agent_mode", "fixture"), "report": value},
                )
            ]
        return []

    if kind == "activity.changed":
        activity = payload.get("activity")
        phase = payload.get("phase")
        body = {
            key: item for key, item in payload.items() if key not in _ACTIVITY_MARKER_KEYS
        }
        if activity == "step" and phase == "started":
            return [("step_started", body)]
        if activity == "step" and phase == "completed":
            return [("step_completed", body)]
        if activity == "report_validation":
            return [("report_validating", body)]
        return []

    if kind == "interaction.changed":
        if payload.get("phase") != "requested":
            return []
        body = {key: item for key, item in payload.items() if key != "phase"}
        return [("clarification_required", body)]

    if kind in {"run.awaiting_input", "run.resumed"}:
        return []

    if kind in {"run.completed", "run.degraded"}:
        status = cast(str, payload.get("status", kind.removeprefix("run.")))
        return [
            (
                "done",
                {"status": status, "limitations": payload.get("limitations", [])},
            )
        ]

    if kind == "run.failed":
        events: list[WireEvent] = []
        error = payload.get("error")
        if isinstance(error, dict):
            events.append(("error", error))
        events.append(
            ("done", {"status": "failed", "limitations": payload.get("limitations", [])})
        )
        return events

    return []


async def restore_wire_events(journal: EventJournal, store: RunStore) -> int:
    """Rebuild replayable wire histories for restored Runs after a restart.

    The snapshot Artifact restores Run state; the journal restores the event
    log so SSE cursor replay keeps working across process restarts.  Runs the
    store does not know, and Runs whose journal history no longer projects
    cleanly, are skipped without failing startup.
    """

    restored = 0
    for run_id in await journal.list_run_ids():
        run_key = str(run_id)
        try:
            events = [event async for event in journal.read(run_id)]
        except UnknownRunError:
            continue
        if not events:
            continue
        try:
            store.get_snapshot(run_key)
        except UnknownRunError:
            if not _register_from_journal(store, run_key, events[0]):
                continue
        wire_history = []
        try:
            for event in events:
                for wire_type, payload in wire_events_for(event):
                    canonical_payload = validate_generic_event(wire_type, payload)
                    wire_history.append((wire_type, canonical_payload, event.occurred_at))
            store.restore_events(run_key, wire_history)
        except (UnknownRunError, ValueError):
            continue
        restored += 1
    return restored


def _register_from_journal(
    store: RunStore,
    run_key: str,
    opened: CanonicalRunEvent,
) -> bool:
    """Recover a journal Run whose snapshot Artifact never made it to disk."""

    if opened.kind != "run.opened":
        return False
    try:
        request = RunRequest.model_validate(opened.payload.get("input"))
        store.register_restored_run(run_key, request, created_at=opened.occurred_at)
    except (ValidationError, ValueError):
        return False
    return True


__all__ = ["WireEvent", "restore_wire_events", "wire_events_for"]
