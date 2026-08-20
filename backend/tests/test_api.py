from __future__ import annotations

import asyncio
import importlib
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

import duckdb
import pytest
from fastapi.testclient import TestClient
from mcp.types import LATEST_PROTOCOL_VERSION

from customer_signal.agent.contracts import RunRequest
from customer_signal.agent.fixture import FixtureRunner
from customer_signal.analytics.service import AnalyticsService
from customer_signal.config import Settings
from customer_signal.data import database as database_module
from customer_signal.data.database import seed_database
from customer_signal.data.repository import DuckDBRepository
from customer_signal.mcp_server import create_mcp_server
from customer_signal.runtime.coordinator import RunCoordinator
from customer_signal.runtime.events import RunnerEvent
from customer_signal.runtime.run_store import RunStore
from customer_signal.synthetic.generator import generate_dataset


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
ALL_SOURCES = ["search_history", "search_feedback", "voc"]
FIVE_SOURCES = [
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
]
TOOL_NAMES = [
    "catalog_sources",
    "aggregate_events",
    "match_journey_pattern",
    "rank_customers",
    "get_customer_journey",
    "get_evidence",
]


def _run_request(*, question: str | None = None) -> dict[str, object]:
    return {
        "question": question or "AI 검색에서 해결하지 못하고 고객센터에 문의한 고객이 몇 명이야?",
        "start_at": START_AT,
        "end_at": END_AT,
        "enabled_sources": ALL_SOURCES,
    }


def _wait_for_terminal(client: TestClient, status_url: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in {"completed", "failed"}:
            return snapshot
        time.sleep(0.01)
    pytest.fail("run did not reach a terminal state")


def _parse_sse(body: str) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    for block in body.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            name, value = line.split(":", maxsplit=1)
            fields[name] = value.lstrip()
        parsed.append(
            {
                "id": int(fields["id"]),
                "event": fields["event"],
                "data": json.loads(fields["data"]),
            }
        )
    return parsed


def _create_app(database_path: Path):
    try:
        module = importlib.import_module("customer_signal.api")
    except ModuleNotFoundError:
        pytest.fail("customer_signal.api must provide the FastAPI app factory")
    return module.create_app(
        Settings(
            agent_mode="fixture",
            database_path=database_path,
            artifact_directory=database_path.parent / "run-artifacts",
            frontend_origin="http://frontend.test",
        )
    )


def _seed_legacy_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE customers (customer_id VARCHAR PRIMARY KEY);
            CREATE TABLE evidence (
                evidence_id VARCHAR PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                masked_customer_id VARCHAR NOT NULL,
                summary VARCHAR NOT NULL,
                raw_fields JSON NOT NULL
            );
            CREATE TABLE events (
                event_id VARCHAR PRIMARY KEY,
                evidence_id VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                event_type VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                topic VARCHAR NOT NULL,
                outcome VARCHAR NOT NULL,
                text VARCHAR NOT NULL,
                canonical_customer_id VARCHAR NOT NULL,
                attributes JSON NOT NULL
            );
            CREATE TABLE ground_truth (customer_id VARCHAR PRIMARY KEY);
            """
        )
        connection.execute("INSERT INTO customers VALUES ('CUST-OLD')")
        for index, (source_id, event_type) in enumerate(
            (
                ("search_history", "search"),
                ("search_feedback", "feedback"),
                ("voc", "voc"),
            ),
            start=1,
        ):
            evidence_id = f"EVD-OLD-{index}"
            connection.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
                [
                    evidence_id,
                    source_id,
                    "2026-08-01T00:00:00+09:00",
                    "CU***OLD",
                    "legacy evidence",
                    "{}",
                ],
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    f"EVT-OLD-{index}",
                    evidence_id,
                    source_id,
                    "2026-08-01T00:00:00+09:00",
                    event_type,
                    "legacy_action",
                    "legacy_topic",
                    "legacy_outcome",
                    "legacy text",
                    "CUST-OLD",
                    "{}",
                ],
            )
        connection.execute("INSERT INTO ground_truth VALUES ('CUST-OLD')")
    finally:
        connection.close()


def _create_blocked_app(database_path: Path):
    module = importlib.import_module("customer_signal.api")
    coordinator_module = importlib.import_module("customer_signal.runtime.coordinator")
    store_module = importlib.import_module("customer_signal.runtime.run_store")

    seed_database(database_path, generate_dataset())
    analytics = AnalyticsService(DuckDBRepository(database_path))
    mcp_server = create_mcp_server(analytics)
    store = store_module.RunStore()

    class BlockingRunner:
        async def run(self, request, *, emit):
            await asyncio.Event().wait()

    coordinator = coordinator_module.RunCoordinator(
        runner=BlockingRunner(),
        analytics=analytics,
        store=store,
    )
    dependencies = module.ApiDependencies(
        store=store,
        coordinator=coordinator,
        mcp_server=mcp_server,
    )
    return module.create_app(
        Settings(
            agent_mode="fixture",
            database_path=database_path,
            artifact_directory=database_path.parent / "run-artifacts",
            frontend_origin="http://frontend.test",
        ),
        dependencies=dependencies,
    )


def _create_routing_probe_app(database_path: Path):
    module = importlib.import_module("customer_signal.api")
    analytics = AnalyticsService(DuckDBRepository(database_path))
    mcp_server = create_mcp_server(analytics)
    store = RunStore()

    class RoutingProbeCoordinator:
        def __init__(self) -> None:
            self.generic_values: list[bool] = []

        def create_run(self, request, *, generic=False, mode=None):
            del request, mode
            self.generic_values.append(generic)

            class Snapshot:
                run_id = "routing-probe-run"

            return Snapshot()

        async def close(self) -> None:
            return None

    coordinator = RoutingProbeCoordinator()
    dependencies = module.ApiDependencies(
        store=store,
        coordinator=coordinator,
        mcp_server=mcp_server,
    )
    app = module.create_app(
        Settings(
            agent_mode="fixture",
            database_path=database_path,
            artifact_directory=database_path.parent / "run-artifacts",
            frontend_origin="http://frontend.test",
        ),
        dependencies=dependencies,
    )
    return app, coordinator


def test_health_uses_factory_without_requiring_an_api_key(tmp_path: Path) -> None:
    database_path = tmp_path / "generated" / "customer-signal.duckdb"

    with TestClient(_create_app(database_path)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert database_path.is_file()


def test_startup_atomically_reseeds_a_legacy_three_source_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "generated" / "customer-signal.duckdb"
    _seed_legacy_database(database_path)
    replacements: list[tuple[Path, Path]] = []
    real_replace = database_module.os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacements.append((source_path, destination_path))
        real_replace(source_path, destination_path)

    monkeypatch.setattr(database_module.os, "replace", recording_replace)

    with TestClient(_create_app(database_path)) as client:
        health = client.get("/health")

    assert health.status_code == 200
    assert len(replacements) == 1
    temporary_path, destination_path = replacements[0]
    assert temporary_path.parent == database_path.parent
    assert temporary_path != database_path
    assert destination_path == database_path
    assert not temporary_path.exists()

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        event_columns = {row[0] for row in connection.execute("DESCRIBE events").fetchall()}
        sources = {
            row[0] for row in connection.execute("SELECT DISTINCT source_id FROM events").fetchall()
        }
    finally:
        connection.close()

    assert tables == {
        "database_metadata",
        "customers",
        "events",
        "evidence",
        "identity_edges",
    }
    assert "ground_truth" not in tables
    assert "identities" in event_columns
    assert sources == set(FIVE_SOURCES)

    with TestClient(_create_app(database_path)) as client:
        accepted = client.post(
            "/api/runs",
            json={**_run_request(), "enabled_sources": FIVE_SOURCES},
        ).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])

    assert snapshot["status"] == "completed"
    assert snapshot["report"]["metrics"][0]["value"] == 6


def test_startup_preserves_an_already_current_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "customer-signal.duckdb"
    seed_database(database_path, generate_dataset())
    before_bytes = database_path.read_bytes()
    before_mtime = database_path.stat().st_mtime_ns
    replacements: list[tuple[Path, Path]] = []
    real_replace = database_module.os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(database_module.os, "replace", recording_replace)

    with TestClient(_create_app(database_path)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert replacements == []
    assert database_path.read_bytes() == before_bytes
    assert database_path.stat().st_mtime_ns == before_mtime


def test_startup_atomically_reseeds_current_version_database_with_wrong_column_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "customer-signal.duckdb"
    seed_database(database_path, generate_dataset())
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("ALTER TABLE events ALTER COLUMN occurred_at SET DATA TYPE VARCHAR")
    finally:
        connection.close()
    replacements: list[tuple[Path, Path]] = []
    real_replace = database_module.os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(database_module.os, "replace", recording_replace)

    assert database_module.is_database_ready(database_path) is False
    with TestClient(_create_app(database_path)) as client:
        health = client.get("/health")
        accepted = client.post(
            "/api/runs",
            json={**_run_request(), "enabled_sources": FIVE_SOURCES},
        ).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])

    assert health.status_code == 200
    database_replacements = [item for item in replacements if item[1] == database_path]
    assert len(database_replacements) == 1
    assert database_replacements[0][0].parent == database_path.parent
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        occurred_at_type = next(
            row[1]
            for row in connection.execute("DESCRIBE events").fetchall()
            if row[0] == "occurred_at"
        )
    finally:
        connection.close()
    assert occurred_at_type == "TIMESTAMP WITH TIME ZONE"
    assert snapshot["status"] == "completed"
    assert snapshot["report"]["metrics"][0]["value"] == 6


def test_startup_safely_replaces_a_malformed_database_file(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-signal.duckdb"
    malformed_bytes = b"not-a-duckdb-file"
    database_path.write_bytes(malformed_bytes)

    with TestClient(_create_app(database_path)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert database_path.read_bytes() != malformed_bytes
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 199
    finally:
        connection.close()


def test_run_completes_with_public_snapshot_and_contiguous_sse(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-signal.duckdb"

    with TestClient(_create_app(database_path)) as client:
        accepted = client.post("/api/runs", json=_run_request())

        assert accepted.status_code == 202
        accepted_body = accepted.json()
        run_id = accepted_body["run_id"]
        assert str(UUID(run_id)) == run_id
        assert accepted_body == {
            "run_id": run_id,
            "status_url": f"/api/runs/{run_id}",
            "events_url": f"/api/runs/{run_id}/events",
        }

        snapshot = _wait_for_terminal(client, accepted_body["status_url"])
        stream = client.get(accepted_body["events_url"])

    assert snapshot["status"] == "completed"
    assert snapshot["agent_mode"] == "fixture"
    assert snapshot["report"]["metrics"][0]["value"] == 6
    assert snapshot["error"] is None
    created_at = datetime.fromisoformat(snapshot["created_at"])
    updated_at = datetime.fromisoformat(snapshot["updated_at"])
    assert created_at.utcoffset() is not None
    assert updated_at.utcoffset() is not None
    assert updated_at >= created_at
    serialized_snapshot = json.dumps(snapshot, ensure_ascii=False).lower()
    for forbidden in ("facts", "tool_result_ids", "raw_fields", "gemini_api_key"):
        assert forbidden not in serialized_snapshot

    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(stream.text)
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    expected_types = ["plan"]
    for _tool_name in TOOL_NAMES:
        expected_types.extend(("tool_started", "tool_completed"))
    expected_types.extend(("validating", "result", "done"))
    assert [event["event"] for event in events] == expected_types
    serialized_events = json.dumps(events, ensure_ascii=False).lower()
    for forbidden in ("facts", "raw_fields", "records", "masked_customer_id", "prompt"):
        assert forbidden not in serialized_events
    for event in events:
        assert event["data"] == {
            "run_id": run_id,
            "type": event["event"],
            "payload": event["data"]["payload"],
        }


def test_last_event_id_replays_only_later_events_and_rejects_bad_cursors(
    tmp_path: Path,
) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post("/api/runs", json=_run_request()).json()
        _wait_for_terminal(client, accepted["status_url"])
        full_events = _parse_sse(client.get(accepted["events_url"]).text)
        cursor = full_events[-3]["id"]

        replay = client.get(
            accepted["events_url"],
            headers={"Last-Event-ID": str(cursor)},
        )
        invalid = [
            client.get(accepted["events_url"], headers={"Last-Event-ID": value})
            for value in ("not-an-int", "-1", str(len(full_events) + 1))
        ]

    replayed = _parse_sse(replay.text)
    assert [event["id"] for event in replayed] == [cursor + 1, cursor + 2]
    assert [event["event"] for event in replayed] == ["result", "done"]
    assert [response.status_code for response in invalid] == [400, 400, 400]


def test_unsupported_question_fails_with_one_error_then_done(tmp_path: Path) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post(
            "/api/runs",
            json=_run_request(question="이번 달 신규 가입 매출을 예측해 줘"),
        ).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])
        events = _parse_sse(client.get(accepted["events_url"]).text)

    assert snapshot["status"] == "failed"
    assert snapshot["run_kind"] == "generic"
    assert snapshot["report"] is None
    assert snapshot["error"]["code"] == "unsupported_analysis"
    assert [event["event"] for event in events] == ["run_started", "error", "done"]
    assert events[-1]["data"]["payload"] == {
        "status": "failed",
        "limitations": [],
    }


@pytest.mark.parametrize(
    "question",
    [
        "부정 피드백 고객은 이후 어떤 행동을 보이고 일반 고객과 무엇이 달라?",
        "최근 이탈 고객의 공통 행동 경로를 Source별로 비교해줘.",
    ],
)
def test_freeform_analysis_questions_route_to_generic_loop(
    tmp_path: Path,
    question: str,
) -> None:
    app, coordinator = _create_routing_probe_app(tmp_path / "routing.duckdb")

    with TestClient(app) as client:
        response = client.post("/api/runs", json=_run_request(question=question))

    assert response.status_code == 202
    assert coordinator.generic_values == [True]


def test_bounded_legacy_journey_question_keeps_legacy_route(tmp_path: Path) -> None:
    app, coordinator = _create_routing_probe_app(tmp_path / "routing.duckdb")

    with TestClient(app) as client:
        response = client.post("/api/runs", json=_run_request())

    assert response.status_code == 202
    assert coordinator.generic_values == [False]


def test_opposite_search_success_question_never_publishes_fixed_failure_report(
    tmp_path: Path,
) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post(
            "/api/runs",
            json=_run_request(question="검색 성공 고객은 몇 명이야?"),
        ).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])
        events = _parse_sse(client.get(accepted["events_url"]).text)

    assert snapshot["status"] == "failed"
    assert snapshot["run_kind"] == "generic"
    assert snapshot["report"] is None
    assert snapshot["error"]["code"] == "unsupported_analysis"
    assert snapshot["error"]["message"] == "현재 안전한 분석 범위에서 지원하지 않는 요청입니다."
    assert [event["event"] for event in events] == ["run_started", "error", "done"]
    assert all(event["event"] != "result" for event in events)


@pytest.mark.parametrize(
    "question",
    [
        "검색 실패 후 문의한 고객의 평균 나이는?",
        "검색 실패 후 문의한 고객의 주소를 알려 줘",
    ],
)
def test_attribute_pivot_never_publishes_fixed_journey_report(
    tmp_path: Path,
    question: str,
) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post(
            "/api/runs",
            json=_run_request(question=question),
        ).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])
        events = _parse_sse(client.get(accepted["events_url"]).text)

    assert snapshot["status"] == "failed"
    assert snapshot["run_kind"] == "generic"
    assert snapshot["report"] is None
    assert snapshot["error"]["code"] == "unsupported_analysis"
    assert snapshot["error"]["message"] == "현재 안전한 분석 범위에서 지원하지 않는 요청입니다."
    assert [event["event"] for event in events] == ["run_started", "error", "done"]
    assert all(event["event"] != "result" for event in events)


@pytest.mark.parametrize(
    "question",
    [
        "검색 실패 후 문의한 고객의 전화가 몇 번이야?",
        "검색 실패 후 문의한 고객의 연락 가능한 번호",
        "검색 실패 후 문의한 고객의 이름",
        "검색 실패 후 문의한 고객에게 전화해 줘",
        "검색에 실패하지 않고 고객센터에 문의한 고객은?",
        "검색 실패가 아닌데 문의한 고객은?",
        "검색에 실패한 고객 중 문의하지 않은 고객은?",
        "검색 실패 후 고객센터에 가지 않은 고객",
    ],
)
def test_semantic_bypass_never_publishes_fixed_journey_report(
    tmp_path: Path,
    question: str,
) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post(
            "/api/runs",
            json=_run_request(question=question),
        ).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])
        events = _parse_sse(client.get(accepted["events_url"]).text)

    assert snapshot["status"] == "failed"
    assert snapshot["run_kind"] == "generic"
    assert snapshot["report"] is None
    assert snapshot["error"]["code"] == "unsupported_analysis"
    assert snapshot["error"]["message"] == "현재 안전한 분석 범위에서 지원하지 않는 요청입니다."
    assert [event["event"] for event in events] == ["run_started", "error", "done"]
    assert all(event["event"] != "result" for event in events)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**_run_request(), "question": "   "},
        {**_run_request(), "start_at": END_AT},
        {**_run_request(), "enabled_sources": ["voc", "voc"]},
    ],
)
def test_run_request_validation_returns_422(tmp_path: Path, payload: dict[str, object]) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        response = client.post("/api/runs", json=payload)

    assert response.status_code == 422


def test_unknown_run_endpoints_return_404(tmp_path: Path) -> None:
    app = _create_app(tmp_path / "customer-signal.duckdb")
    with TestClient(app, raise_server_exceptions=False) as client:
        snapshot = client.get("/api/runs/unknown")
        events = client.get("/api/runs/unknown/events")
        journey = client.get("/api/runs/unknown/customers/CUST-003/journey")
        evidence = client.get("/api/runs/unknown/evidence/EVD-UNKNOWN")

    assert snapshot.status_code == 404
    assert events.status_code == 404
    assert journey.status_code == 404
    assert evidence.status_code == 404


def test_completed_run_serves_another_ranked_journey_and_masked_evidence(
    tmp_path: Path,
) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post("/api/runs", json=_run_request()).json()
        snapshot = _wait_for_terminal(client, accepted["status_url"])
        another_customer = snapshot["report"]["ranked_customers"][1]["customer_id"]

        journey = client.get(f"/api/runs/{accepted['run_id']}/customers/{another_customer}/journey")
        assert journey.status_code == 200
        journey_body = journey.json()
        evidence_id = journey_body["evidence_ids"][0]
        evidence = client.get(f"/api/runs/{accepted['run_id']}/evidence/{evidence_id}")
        guessed_customer = client.get(f"/api/runs/{accepted['run_id']}/customers/CUST-001/journey")
        guessed_evidence = client.get(f"/api/runs/{accepted['run_id']}/evidence/EVD-GUESSED")

    assert journey_body["customer_id"] == another_customer
    assert journey_body["events"]
    assert evidence.status_code == 200
    assert evidence.json()["evidence_ids"] == [evidence_id]
    serialized_evidence = json.dumps(evidence.json(), ensure_ascii=False)
    assert "CU***" in serialized_evidence
    assert "CUST-" not in serialized_evidence
    assert guessed_customer.status_code == 404
    assert guessed_evidence.status_code == 404


def test_journey_authorized_evidence_cannot_cross_runs(tmp_path: Path) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        first = client.post("/api/runs", json=_run_request()).json()
        second = client.post("/api/runs", json=_run_request()).json()
        _wait_for_terminal(client, first["status_url"])
        _wait_for_terminal(client, second["status_url"])

        extra_journey = client.get(f"/api/runs/{first['run_id']}/customers/CUST-002/journey")
        assert extra_journey.status_code == 200
        journey_only_evidence = extra_journey.json()["evidence_ids"][0]

        first_access = client.get(f"/api/runs/{first['run_id']}/evidence/{journey_only_evidence}")
        cross_run_access = client.get(
            f"/api/runs/{second['run_id']}/evidence/{journey_only_evidence}"
        )

    assert first_access.status_code == 200
    assert cross_run_access.status_code == 404


def test_details_require_a_completed_run(tmp_path: Path) -> None:
    with TestClient(_create_blocked_app(tmp_path / "customer-signal.duckdb")) as client:
        accepted = client.post("/api/runs", json=_run_request()).json()
        journey = client.get(f"/api/runs/{accepted['run_id']}/customers/CUST-003/journey")
        evidence = client.get(f"/api/runs/{accepted['run_id']}/evidence/EVD-20260819-003-01")

    assert journey.status_code == 409
    assert evidence.status_code == 409


def test_concurrent_runs_keep_independent_event_sequences(tmp_path: Path) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        first = client.post("/api/runs", json=_run_request()).json()
        second = client.post("/api/runs", json=_run_request()).json()
        _wait_for_terminal(client, first["status_url"])
        _wait_for_terminal(client, second["status_url"])
        first_events = _parse_sse(client.get(first["events_url"]).text)
        second_events = _parse_sse(client.get(second["events_url"]).text)

    assert first["run_id"] != second["run_id"]
    assert [event["id"] for event in first_events] == list(range(1, len(first_events) + 1))
    assert [event["id"] for event in second_events] == list(range(1, len(second_events) + 1))
    assert {event["data"]["run_id"] for event in first_events} == {first["run_id"]}
    assert {event["data"]["run_id"] for event in second_events} == {second["run_id"]}


def test_cors_allows_only_the_configured_frontend_origin(tmp_path: Path) -> None:
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        allowed = client.options(
            "/api/runs",
            headers={
                "Origin": "http://frontend.test",
                "Access-Control-Request-Method": "POST",
            },
        )
        denied = client.options(
            "/api/runs",
            headers={
                "Origin": "http://untrusted.test",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://frontend.test"
    assert "access-control-allow-origin" not in denied.headers


def test_mounted_mcp_http_app_runs_its_lifespan(tmp_path: Path) -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "api-test", "version": "1.0"},
        },
    }
    with TestClient(_create_app(tmp_path / "customer-signal.duckdb")) as client:
        response = client.post(
            "/mcp/",
            json=initialize,
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 200
    assert "Customer Signal Data" in response.text
    assert LATEST_PROTOCOL_VERSION in response.text


def test_importing_api_does_not_seed_the_default_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("customer_signal.api")

    importlib.reload(module)

    assert not (tmp_path / "data" / "generated" / "customer_signal.duckdb").exists()


async def test_coordinator_shutdown_marks_active_run_failed_and_closes_stream(
    database_path: Path,
) -> None:
    analytics = AnalyticsService(DuckDBRepository(database_path))
    store = RunStore()

    class BlockingRunner:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def run(self, request, *, emit):
            self.started.set()
            await asyncio.Event().wait()

    runner = BlockingRunner()
    coordinator = RunCoordinator(runner=runner, analytics=analytics, store=store)
    snapshot = coordinator.create_run(RunRequest.model_validate(_run_request()))
    await asyncio.wait_for(runner.started.wait(), timeout=1)

    await coordinator.close()

    terminal = store.get_snapshot(snapshot.run_id)
    assert terminal.status == "failed"
    assert terminal.error is not None
    assert terminal.error.code == "run_cancelled"
    events = [event async for event in store.stream_events(snapshot.run_id)]
    assert [event.type for event in events] == ["error", "done"]


async def test_runner_reported_error_wins_over_returned_outcome(database_path: Path) -> None:
    analytics = AnalyticsService(DuckDBRepository(database_path))
    mcp_server = create_mcp_server(analytics)
    request = RunRequest.model_validate(_run_request())
    outcome = await FixtureRunner(mcp_server).run(request, emit=lambda _event: None)

    class ErrorThenOutcomeRunner:
        async def run(self, request, *, emit):
            await emit(
                RunnerEvent(
                    type="error",
                    payload={
                        "code": "tool_execution_failed",
                        "message": "internal detail must stay private",
                    },
                )
            )
            await emit(
                RunnerEvent(
                    type="result",
                    payload={
                        "agent_mode": outcome.agent_mode,
                        "report": outcome.report.model_dump(mode="json"),
                    },
                )
            )
            return outcome

    store = RunStore()
    coordinator = RunCoordinator(
        runner=ErrorThenOutcomeRunner(),
        analytics=analytics,
        store=store,
    )
    created = coordinator.create_run(request)

    terminal = await coordinator.wait_for_run(created.run_id)
    events = [event async for event in store.stream_events(created.run_id)]

    assert terminal.status == "failed"
    assert terminal.report is None
    assert terminal.error is not None
    assert terminal.error.code == "tool_execution_failed"
    assert [event.type for event in events] == ["error", "done"]
    assert events[-1].payload == {"status": "failed"}


async def test_immediate_shutdown_finalizes_queued_run(database_path: Path) -> None:
    analytics = AnalyticsService(DuckDBRepository(database_path))

    class ShouldNotStartRunner:
        def __init__(self) -> None:
            self.started = False

        async def run(self, request, *, emit):
            self.started = True
            await asyncio.Event().wait()

    runner = ShouldNotStartRunner()
    store = RunStore()
    coordinator = RunCoordinator(runner=runner, analytics=analytics, store=store)
    created = coordinator.create_run(RunRequest.model_validate(_run_request()))

    await coordinator.close()

    terminal = store.get_snapshot(created.run_id)
    assert runner.started is False
    assert terminal.status == "failed"
    assert terminal.error is not None
    assert terminal.error.code == "run_cancelled"
    events = [event async for event in store.stream_events(created.run_id)]
    assert [event.type for event in events] == ["error", "done"]
