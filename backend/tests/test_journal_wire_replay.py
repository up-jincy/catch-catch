"""Wire projection contract and journal-backed SSE replay across restarts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from customer_signal.agent.generic_fixture import NEGATIVE_TOPIC_QUESTION
from customer_signal.api import create_app
from customer_signal.config import Settings
from customer_signal.journal.events import CanonicalRunEvent, PackRef, VersionedValue
from customer_signal.runtime.wire_projection import wire_events_for

START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
SOURCES = [
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
]

PACK = PackRef(
    pack_id="customer_signal",
    pack_version="1.0.0",
    contract_digest="digest-0001",
)


def canonical(kind: str, payload=None, artifact=None, sequence: int = 1) -> CanonicalRunEvent:
    event_id = uuid4()
    return CanonicalRunEvent(
        event_id=event_id,
        run_id=uuid4(),
        sequence=sequence,
        occurred_at=datetime.now(timezone.utc),
        pack=PACK,
        kind=kind,
        artifact=artifact,
        payload=payload or {},
        causation_id=event_id,
        correlation_id=uuid4(),
    )


def artifact_value(kind: str, value) -> VersionedValue:
    return VersionedValue(
        schema_id=f"customer_signal.{kind}.v1",
        schema_digest="digest-0002",
        value=value,
    )


def test_wire_projection_maps_every_canonical_kind() -> None:
    goal = {"goal_id": "goal-negative"}
    assert wire_events_for(
        canonical("run.opened", {"status": "running", "input": {"question": "질문"}})
    ) == [("run_started", {"status": "running"})]
    assert wire_events_for(
        canonical(
            "artifact.committed",
            {"artifact_kind": "goal"},
            artifact_value("goal", goal),
        )
    ) == [("goal_created", {"goal": goal})]
    assert wire_events_for(
        canonical(
            "artifact.committed",
            {"artifact_kind": "plan", "revised": True},
            artifact_value("plan", {"plan_id": "plan-1"}),
        )
    ) == [("plan_revised", {"plan": {"plan_id": "plan-1"}})]
    assert wire_events_for(
        canonical(
            "artifact.committed",
            {"artifact_kind": "fact", "step_id": "step-1"},
            artifact_value("fact", {"fact_id": "fact-1"}),
        )
    ) == [("fact_created", {"step_id": "step-1", "fact": {"fact_id": "fact-1"}})]
    assert wire_events_for(
        canonical(
            "artifact.committed",
            {"artifact_kind": "note"},
            artifact_value("note", {"note_id": "note-1"}),
        )
    ) == [("analysis_note_created", {"note": {"note_id": "note-1"}})]
    assert wire_events_for(
        canonical(
            "artifact.committed",
            {"artifact_kind": "report", "agent_mode": "fixture"},
            artifact_value("report", {"headline": "결론"}),
        )
    ) == [("result", {"agent_mode": "fixture", "report": {"headline": "결론"}})]
    assert wire_events_for(
        canonical(
            "activity.changed",
            {
                "activity": "step",
                "phase": "started",
                "step_id": "step-1",
                "primitive": "catalog_sources",
                "selection_reason": "이유",
                "started_at": "2026-08-20T01:00:00Z",
            },
        )
    ) == [
        (
            "step_started",
            {
                "step_id": "step-1",
                "primitive": "catalog_sources",
                "selection_reason": "이유",
                "started_at": "2026-08-20T01:00:00Z",
            },
        )
    ]
    assert wire_events_for(
        canonical(
            "activity.changed",
            {"activity": "report_validation", "fact_ids": [], "result_ids": []},
        )
    ) == [("report_validating", {"fact_ids": [], "result_ids": []})]
    assert wire_events_for(
        canonical(
            "interaction.changed",
            {
                "phase": "requested",
                "kind": "clarification",
                "clarification_id": "clar-1",
                "question": "기간?",
            },
        )
    ) == [
        (
            "clarification_required",
            {"kind": "clarification", "clarification_id": "clar-1", "question": "기간?"},
        )
    ]
    assert wire_events_for(canonical("interaction.changed", {"phase": "answered"})) == []
    assert wire_events_for(canonical("run.awaiting_input", {"status": "x"})) == []
    assert wire_events_for(canonical("run.resumed")) == []
    assert wire_events_for(
        canonical("run.completed", {"status": "completed", "limitations": []})
    ) == [("done", {"status": "completed", "limitations": []})]
    assert wire_events_for(
        canonical("run.degraded", {"status": "degraded", "limitations": ["제한"]})
    ) == [("done", {"status": "degraded", "limitations": ["제한"]})]
    failed = wire_events_for(
        canonical(
            "run.failed",
            {
                "status": "failed",
                "limitations": [],
                "error": {"code": "analysis_failed", "message": "실패"},
            },
        )
    )
    assert failed == [
        ("error", {"code": "analysis_failed", "message": "실패"}),
        ("done", {"status": "failed", "limitations": []}),
    ]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        agent_mode="fixture",
        database_path=tmp_path / "customer-signal.duckdb",
        artifact_directory=tmp_path / "artifacts",
        onboarded_sources_dir=tmp_path / "onboarded-sources",
        frontend_origin="http://frontend.test",
        _env_file=None,
    )


def _events(client: TestClient, events_url: str, headers=None) -> list[dict[str, object]]:
    response = client.get(events_url, headers=headers or {})
    assert response.status_code == 200
    parsed = []
    for block in response.text.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, value = line.split(":", maxsplit=1)
            fields[key] = value.lstrip()
        parsed.append(
            {
                "id": int(fields["id"]),
                "type": fields["event"],
                "envelope": json.loads(fields["data"]),
            }
        )
    return parsed


def test_sse_cursor_replay_survives_backend_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = {
        "question": NEGATIVE_TOPIC_QUESTION,
        "start_at": START_AT,
        "end_at": END_AT,
        "enabled_sources": SOURCES,
    }
    with TestClient(create_app(settings)) as client:
        accepted = client.post("/api/runs", json=request).json()
        import time

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get(accepted["status_url"]).json()["status"] == "completed":
                break
            time.sleep(0.01)
        original = _events(client, accepted["events_url"])

    assert original[0]["type"] == "run_started"
    assert original[-1]["type"] == "done"

    with TestClient(create_app(settings)) as restarted:
        replayed = _events(restarted, accepted["events_url"])
        resumed = _events(
            restarted,
            accepted["events_url"],
            headers={"Last-Event-ID": "3"},
        )
        snapshot = restarted.get(accepted["status_url"]).json()

    assert [(event["id"], event["type"]) for event in replayed] == [
        (event["id"], event["type"]) for event in original
    ]
    assert [event["envelope"] for event in replayed] == [
        event["envelope"] for event in original
    ]
    assert [event["id"] for event in resumed] == list(
        range(4, original[-1]["id"] + 1)
    )
    assert snapshot["status"] == "completed"
    assert snapshot["last_event_id"] == original[-1]["id"]
