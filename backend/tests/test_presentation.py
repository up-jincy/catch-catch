"""Presentation projection purity, fallback behavior, and the replay endpoint."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from customer_signal.agent.generic_fixture import NEGATIVE_TOPIC_QUESTION
from customer_signal.api import create_app
from customer_signal.config import Settings
from customer_signal.journal.events import CanonicalRunEvent, PackRef, VersionedValue
from customer_signal.presentation.generic import GenericRunProjector
from customer_signal.presentation.intents import TRUSTED_CATALOG_KEYS
from customer_signal.presentation.projector import fold_intents

PACK = PackRef(
    pack_id="customer_signal",
    pack_version="1.0.0",
    contract_digest="digest-0001",
)


def canonical(sequence: int, kind: str, payload=None, artifact=None) -> CanonicalRunEvent:
    event_id = uuid4()
    return CanonicalRunEvent(
        event_id=event_id,
        run_id=uuid4(),
        sequence=sequence,
        occurred_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        pack=PACK,
        kind=kind,
        artifact=artifact,
        payload=payload or {},
        causation_id=event_id,
        correlation_id=uuid4(),
    )


def lifecycle_events() -> list[CanonicalRunEvent]:
    return [
        canonical(1, "run.opened", {"status": "running"}),
        canonical(
            2,
            "artifact.committed",
            {"artifact_kind": "goal"},
            VersionedValue(
                schema_id="customer_signal.goal.v1",
                schema_digest="digest-0002",
                value={"goal_id": "goal-1"},
            ),
        ),
        canonical(
            3,
            "activity.changed",
            {"activity": "step", "phase": "started", "step_id": "step-1"},
        ),
        canonical(4, "run.completed", {"status": "completed", "limitations": []}),
    ]


def test_generic_projector_is_pure_and_deterministic() -> None:
    events = lifecycle_events()
    projector = GenericRunProjector()
    first = fold_intents(projector, events)
    second = fold_intents(projector, events)
    assert first == second
    assert first[0].kind == "open"
    assert first[0].surface_key == "run"
    assert first[-1].kind == "close"
    for intent in first:
        if intent.catalog_key is not None:
            assert intent.catalog_key in TRUSTED_CATALOG_KEYS
        json.dumps(intent.model_dump(mode="json"))


def test_unknown_artifact_kind_projects_a_diagnostic_notice() -> None:
    events = [
        canonical(1, "run.opened", {"status": "running"}),
        canonical(
            2,
            "artifact.committed",
            {"artifact_kind": "hologram"},
            VersionedValue(
                schema_id="future_pack.hologram.v1",
                schema_digest="digest-0003",
                value={"shape": "cube"},
            ),
        ),
    ]
    intents = fold_intents(GenericRunProjector(), events)
    notice = intents[-1]
    assert notice.kind == "notice"
    assert notice.surface_key == "run.diagnostics"
    assert notice.body["artifact_kind"] == "hologram"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        agent_mode="fixture",
        database_path=tmp_path / "customer-signal.duckdb",
        artifact_directory=tmp_path / "artifacts",
        onboarded_sources_dir=tmp_path / "onboarded-sources",
        frontend_origin="http://frontend.test",
        _env_file=None,
    )


def test_presentation_replay_endpoint_recomputes_from_the_journal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = {
        "question": NEGATIVE_TOPIC_QUESTION,
        "start_at": "2026-07-20T00:00:00+09:00",
        "end_at": "2026-08-19T00:00:00+09:00",
        "enabled_sources": [
            "search_history",
            "search_feedback",
            "digital_behavior",
            "subscription",
            "voc",
        ],
    }
    with TestClient(create_app(settings)) as client:
        accepted = client.post("/api/runs", json=request).json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get(accepted["status_url"]).json()["status"] == "completed":
                break
            time.sleep(0.01)
        live = client.get(f"/api/runs/{accepted['run_id']}/presentation")
        missing = client.get(f"/api/runs/{uuid4()}/presentation")
        invalid = client.get("/api/runs/not-a-uuid/presentation")

    assert live.status_code == 200
    body = live.json()
    assert body["run_id"] == accepted["run_id"]
    assert body["pack_id"] == "customer_signal"
    kinds = [intent["kind"] for intent in body["intents"]]
    assert kinds[0] == "open"
    assert kinds[-1] == "close"
    assert any(intent["surface_key"] == "run.report" for intent in body["intents"])
    assert missing.status_code == 404
    assert invalid.status_code == 404

    with TestClient(create_app(settings)) as restarted:
        replayed = restarted.get(f"/api/runs/{accepted['run_id']}/presentation")
    assert replayed.status_code == 200
    assert replayed.json() == body
