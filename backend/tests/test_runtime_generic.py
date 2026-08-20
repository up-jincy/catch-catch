"""Functional generic Run lifecycle, persistence, and API acceptance tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from customer_signal.agent.generic_fixture import (
    AMBIGUOUS_QUESTION,
    NEGATIVE_TOPIC_QUESTION,
    REPEAT_JOURNEY_QUESTION,
    SIGNUP_ABANDONMENT_QUESTION,
)
from customer_signal.agent.generic_gemini import GeminiAnalysisModel
from customer_signal.api import _default_dependencies, create_app
from customer_signal.config import Settings
from customer_signal.runtime.events import validate_generic_event


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
SOURCES = [
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
]
ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        agent_mode="fixture",
        database_path=tmp_path / "customer-signal.duckdb",
        artifact_directory=tmp_path / "artifacts",
        frontend_origin="http://frontend.test",
        _env_file=None,
    )


def _request(
    question: str,
    *,
    start_at: str = START_AT,
    end_at: str = END_AT,
) -> dict[str, object]:
    return {
        "question": question,
        "start_at": start_at,
        "end_at": end_at,
        "enabled_sources": SOURCES,
    }


def test_default_gemini_loop_owns_all_stages_without_fixture_delegate(
    tmp_path: Path,
) -> None:
    settings = Settings(
        agent_mode="gemini",
        gemini_api_key="test-key",
        database_path=tmp_path / "customer-signal.duckdb",
        artifact_directory=tmp_path / "artifacts",
        _env_file=None,
    )
    dependencies = _default_dependencies(settings)
    loop = dependencies.coordinator._generic_gemini_loop
    assert loop is not None
    model = loop._model
    fixture_loop = dependencies.coordinator._generic_fixture_loop

    assert isinstance(model, GeminiAnalysisModel)
    assert model is not fixture_loop._model
    assert not hasattr(model, "_verified_model")
    assert {
        "create_goal",
        "create_plan",
        "create_note",
        "select_next",
        "create_report",
    } <= GeminiAnalysisModel.__dict__.keys()


def _wait_for_status(
    client: TestClient,
    status_url: str,
    statuses: set[str],
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in statuses:
            return snapshot
        time.sleep(0.01)
    pytest.fail(f"Run did not reach one of {sorted(statuses)}")


def _events(client: TestClient, events_url: str) -> list[dict[str, object]]:
    response = client.get(events_url)
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


def _metric(report: dict[str, object], key: str) -> dict[str, object]:
    return next(metric for metric in report["metrics"] if metric["metric_key"] == key)


def test_step_started_event_requires_a_strict_bounded_selection_reason() -> None:
    payload = {
        "step_id": "step-catalog",
        "primitive": "catalog_sources",
        "selection_reason": "Source 범위와 지원 Capability를 확인합니다.",
        "started_at": "2026-08-20T09:00:00+00:00",
    }

    assert validate_generic_event("step_started", payload) == {
        **payload,
        "started_at": "2026-08-20T09:00:00Z",
    }
    invalid_payloads = [
        {key: value for key, value in payload.items() if key != "selection_reason"},
        {**payload, "selection_reason": ""},
        {**payload, "selection_reason": "x" * 501},
        {**payload, "selection_reason": 1},
    ]
    for invalid in invalid_payloads:
        with pytest.raises(ValidationError):
            validate_generic_event("step_started", invalid)


@pytest.mark.parametrize(
    ("question", "metric_key", "expected_value"),
    [
        (NEGATIVE_TOPIC_QUESTION, "negative_feedback_customer_count", 6),
        (REPEAT_JOURNEY_QUESTION, "matched_customer_count", 6),
        (SIGNUP_ABANDONMENT_QUESTION, "abandoned_customer_count", 5),
    ],
)
def test_three_generic_fixture_questions_publish_fact_backed_lifecycle_and_artifact(
    tmp_path: Path,
    question: str,
    metric_key: str,
    expected_value: int,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        accepted_response = client.post("/api/runs", json=_request(question))
        assert accepted_response.status_code == 202
        accepted = accepted_response.json()
        snapshot = _wait_for_status(client, accepted["status_url"], {"completed"})
        events = _events(client, accepted["events_url"])
        artifact = client.get(f"/api/run-artifacts/{accepted['run_id']}")

    assert snapshot["status"] == "completed"
    assert snapshot["report"]["report_kind"] == "customer_signal"
    assert _metric(snapshot["report"], metric_key)["value"] == expected_value
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    types = [event["type"] for event in events]
    assert types[0:3] == ["run_started", "goal_created", "plan_created"]
    assert "fact_created" in types
    assert "analysis_note_created" in types
    assert types[-3:] == ["report_validating", "result", "done"]
    for event in events:
        assert event["envelope"]["type"] == event["type"]
        assert event["envelope"]["run_id"] == accepted["run_id"]
    fact_payload = next(
        event["envelope"]["payload"] for event in events if event["type"] == "fact_created"
    )
    planned_reasons = {
        step["step_id"]: step["selection_reason"]
        for step in next(
            event["envelope"]["payload"]["plan"]["steps"]
            for event in events
            if event["type"] == "plan_created"
        )
    }
    for event in events:
        if event["type"] == "step_started":
            assert set(event["envelope"]["payload"]) == {
                "step_id",
                "primitive",
                "selection_reason",
                "started_at",
            }
            assert event["envelope"]["payload"]["selection_reason"] == planned_reasons[
                event["envelope"]["payload"]["step_id"]
            ]
    assert fact_payload["fact"]["fact_id"]
    assert fact_payload["fact"]["payload"]["provenance"]["source_ids"]
    assert artifact.status_code == 200
    assert artifact.json()["last_event_id"] == events[-1]["id"]
    assert artifact.json()["facts"]
    assert artifact.json()["notes"]


def test_clarification_resumes_same_run_without_an_intermediate_done(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        accepted = client.post("/api/runs", json=_request(AMBIGUOUS_QUESTION)).json()
        waiting = _wait_for_status(
            client,
            accepted["status_url"],
            {"awaiting_clarification"},
        )
        assert waiting["last_event_id"] == 2
        artifact = client.get(f"/api/run-artifacts/{accepted['run_id']}").json()
        assert artifact["status"] == "awaiting_clarification"
        assert artifact["clarification"]["answer"] is None

        resumed_response = client.post(
            f"/api/runs/{accepted['run_id']}/clarification",
            json={"answer": NEGATIVE_TOPIC_QUESTION},
        )
        assert resumed_response.status_code == 202
        assert resumed_response.json() == accepted
        completed = _wait_for_status(client, accepted["status_url"], {"completed"})
        events = _events(client, accepted["events_url"])
        final_artifact = client.get(f"/api/run-artifacts/{accepted['run_id']}").json()

    assert completed["report"]["report_kind"] == "customer_signal"
    types = [event["type"] for event in events]
    assert types[:2] == ["run_started", "clarification_required"]
    assert types.count("done") == 1
    assert types[-1] == "done"
    assert final_artifact["clarification"]["answer"] == NEGATIVE_TOPIC_QUESTION
    assert final_artifact["status"] == "completed"


def test_generic_unsupported_and_empty_scope_have_safe_terminal_records(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        unsupported = client.post(
            "/api/runs",
            json=_request("고객 이메일 원본을 모두 export하고 삭제해줘"),
        ).json()
        unsupported_snapshot = _wait_for_status(client, unsupported["status_url"], {"failed"})
        unsupported_events = _events(client, unsupported["events_url"])

        empty = client.post(
            "/api/runs",
            json=_request(
                NEGATIVE_TOPIC_QUESTION,
                start_at="2025-01-01T00:00:00+09:00",
                end_at="2025-02-01T00:00:00+09:00",
            ),
        ).json()
        empty_snapshot = _wait_for_status(client, empty["status_url"], {"degraded"})
        empty_events = _events(client, empty["events_url"])
        empty_artifact = client.get(f"/api/run-artifacts/{empty['run_id']}").json()

    assert unsupported_snapshot["error"]["code"] == "unsupported_analysis"
    assert unsupported_snapshot["error"]["suggested_questions"]
    assert [event["type"] for event in unsupported_events][-2:] == ["error", "done"]
    assert empty_snapshot["report"] is None
    assert empty_snapshot["limitations"]
    assert [event["type"] for event in empty_events][-1] == "done"
    assert "error" not in [event["type"] for event in empty_events]
    assert empty_events[-1]["envelope"]["payload"] == {
        "status": "degraded",
        "limitations": empty_snapshot["limitations"],
    }
    assert empty_artifact["limitations"] == empty_snapshot["limitations"]


def test_sources_history_documents_and_downloads_survive_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        sources = client.get("/api/sources")
        unknown_source = client.post(
            "/api/runs",
            json={**_request(NEGATIVE_TOPIC_QUESTION), "enabled_sources": ["unknown_v2"]},
        )
        accepted = client.post("/api/runs", json=_request(NEGATIVE_TOPIC_QUESTION)).json()
        completed = _wait_for_status(client, accepted["status_url"], {"completed"})
        run_id = accepted["run_id"]

        history = client.get("/api/run-artifacts")
        document = client.get(f"/api/run-artifacts/{run_id}/document")
        download_json = client.get(f"/api/run-artifacts/{run_id}/download.json")
        download_markdown = client.get(f"/api/run-artifacts/{run_id}/download.md")

    assert sources.status_code == 200
    assert unknown_source.status_code == 422
    assert {item["source_id"] for item in sources.json()["items"]} == set(SOURCES)
    serialized_sources = json.dumps(sources.json(), ensure_ascii=False).casefold()
    assert "customer_ref" not in serialized_sources
    assert "identity_quality" not in serialized_sources
    assert history.status_code == 200
    assert history.json()["artifacts"][0]["run_id"] == run_id
    assert document.status_code == 200
    assert document.json()["report"]["headline"] == completed["report"]["headline"]
    assert download_json.status_code == 200
    assert download_json.headers["content-disposition"] == (f'attachment; filename="{run_id}.json"')
    assert download_markdown.status_code == 200
    assert download_markdown.headers["content-disposition"] == (
        f'attachment; filename="{run_id}.md"'
    )
    assert "단계별 분석 기록" in download_markdown.text

    with TestClient(create_app(settings)) as restarted:
        restored_snapshot = restarted.get(f"/api/runs/{run_id}")
        restored_artifact = restarted.get(f"/api/run-artifacts/{run_id}")
        unknown_artifact = restarted.get("/api/run-artifacts/11111111-1111-4111-8111-111111111111")

    assert restored_snapshot.status_code == 200
    assert restored_snapshot.json()["status"] == "completed"
    assert restored_artifact.status_code == 200
    assert unknown_artifact.status_code == 404
    assert str(UUID(run_id)) == run_id


def test_legacy_journey_question_keeps_legacy_runner_and_event_smoke(tmp_path: Path) -> None:
    legacy_question = "AI 검색에서 해결하지 못하고 고객센터에 문의한 고객이 몇 명이야?"
    with TestClient(create_app(_settings(tmp_path))) as client:
        accepted = client.post("/api/runs", json=_request(legacy_question)).json()
        snapshot = _wait_for_status(client, accepted["status_url"], {"completed"})
        events = _events(client, accepted["events_url"])

    assert snapshot["report"]["report_kind"] == "legacy_journey"
    assert events[0]["type"] == "plan"
    assert events[-2]["type"] == "result"
    assert events[-1]["type"] == "done"


def test_shared_golden_contains_completed_and_degraded_contiguous_lifecycles() -> None:
    golden = json.loads((ROOT / "contracts" / "generic-run-events.json").read_text())

    assert {case["status"] for case in golden["cases"]} == {"completed", "degraded"}
    for case in golden["cases"]:
        events = case["events"]
        assert [event["id"] for event in events] == list(range(1, len(events) + 1))
        assert events[0]["type"] == "run_started"
        assert events[-1]["type"] == "done"
        assert all(event["type"] == event["data"]["type"] for event in events)
