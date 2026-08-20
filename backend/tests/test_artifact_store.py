"""Persistent, public-safe Run Artifact contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from customer_signal.agent.contracts import RunRequest
from customer_signal.config import Settings
from customer_signal.domain.analysis import (
    AnalysisPlan,
    AnalysisStep,
    ContinueAfterStep,
    ExpectedOutputSpec,
    PublicRunError,
    StepLimits,
)
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    ProfileEventsInput,
)
from customer_signal.domain.reports import AnalysisScope, InsightReport
from customer_signal.runtime.artifact_store import (
    ArtifactStore,
    ArtifactWriteError,
    InvalidRunIdError,
    UnsafeArtifactDataError,
    UnsupportedArtifactVersionError,
)
from customer_signal.runtime.artifacts import (
    ClarificationRecord,
    RunArtifact,
    RunVersions,
    artifact_json_bytes,
)


NOW = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
LIMITS = StepLimits(
    max_input_events=100,
    max_output_rows=20,
    max_evidence=5,
    timeout_seconds=10.0,
)


def _request() -> RunRequest:
    return RunRequest(
        question="최근 고객 신호를 분석해줘",
        start_at=NOW - timedelta(days=30),
        end_at=NOW,
        enabled_sources=["voc"],
    )


def _versions() -> RunVersions:
    return RunVersions(
        dataset_versions=["dataset-1"],
        adapter_versions={"voc": "adapter-1"},
        manifest_versions={"voc": "manifest-1"},
        prompt_version="prompt-1",
        model_version="gemini-3.7-flash",
    )


def _legacy_report() -> InsightReport:
    return InsightReport(
        analysis_type="general",
        scope=AnalysisScope(
            start_at=NOW - timedelta(days=30),
            end_at=NOW,
            enabled_sources=["voc"],
            population_description="선택 기간 고객",
        ),
        headline="검증된 고객 신호",
        executive_summary="공개 집계만 사용했습니다.",
        sources_used=["voc"],
        limitations=[],
    )


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="plan-artifact",
        revision=0,
        goal_id="goal-artifact",
        steps=[
            AnalysisStep(
                step_id="step-catalog",
                primitive="catalog_sources",
                parameters=CatalogSourcesInput(primitive="catalog_sources"),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="catalog_sources", required_metric_keys=["source_count"]
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
            AnalysisStep(
                step_id="step-profile",
                primitive="profile_events",
                parameters=ProfileEventsInput(primitive="profile_events"),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="profile_events",
                    required_metric_keys=["customer_count", "event_count"],
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
            AnalysisStep(
                step_id="step-aggregate",
                primitive="aggregate_events",
                parameters=AggregateEventsInput(
                    primitive="aggregate_events",
                    aggregation="count",
                    time_grain="day",
                ),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="aggregate_events", required_metric_keys=["event_count"]
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
        ],
    )


def _artifact(
    *,
    run_id: UUID = RUN_ID,
    status: str = "running",
    updated_at: datetime = NOW,
) -> RunArtifact:
    terminal = status in {"completed", "degraded", "failed"}
    return RunArtifact(
        run_id=run_id,
        status=status,
        created_at=NOW - timedelta(minutes=1),
        updated_at=updated_at,
        completed_at=updated_at if terminal else None,
        request=_request(),
        clarification=(
            ClarificationRecord(
                clarification_id="clarification-1",
                question="기간을 확인해 주세요.",
            )
            if status == "awaiting_clarification"
            else None
        ),
        report=_legacy_report() if status == "completed" else None,
        last_event_id=4,
        versions=_versions(),
        limitations=["부분 결과만 기록했습니다."] if status == "degraded" else [],
        error=(
            PublicRunError(code="analysis_failed", message="안전한 공개 오류")
            if status == "failed"
            else None
        ),
    )


@pytest.mark.parametrize(
    "artifact",
    [
        _artifact(status="running"),
        _artifact(status="awaiting_clarification"),
        _artifact(status="completed"),
        _artifact(status="degraded"),
        _artifact(status="failed"),
    ],
)
def test_store_round_trips_partial_and_terminal_artifacts_atomically(
    tmp_path: Path, artifact: RunArtifact
) -> None:
    store = ArtifactStore(tmp_path)

    store.save(artifact)

    assert store.load(artifact.run_id) == artifact
    assert RunArtifact.model_validate_json(store.load_bytes(artifact.run_id)) == artifact
    assert store.load_bytes(artifact.run_id) == artifact_json_bytes(artifact)
    assert list(tmp_path.glob("*.tmp")) == []


def test_store_lists_newest_updated_artifact_and_uses_settings_without_eager_write(
    tmp_path: Path,
) -> None:
    artifact_directory = tmp_path / "nested" / "run-artifacts"
    settings = Settings(artifact_directory=artifact_directory, _env_file=None)
    store = ArtifactStore.from_settings(settings)
    assert store.directory == artifact_directory
    assert not artifact_directory.exists()

    older = _artifact(run_id=RUN_ID, updated_at=NOW)
    newer = _artifact(run_id=OTHER_RUN_ID, updated_at=NOW + timedelta(seconds=1))
    store.save(older)
    store.save(newer)

    assert [artifact.run_id for artifact in store.list()] == [OTHER_RUN_ID, RUN_ID]
    assert [summary.run_id for summary in store.list_summaries()] == [OTHER_RUN_ID, RUN_ID]


def test_store_rejects_unsafe_uuid_and_unsupported_schema(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(InvalidRunIdError):
        store.load("../../outside")

    artifact = _artifact()
    store.save(artifact)
    target = tmp_path / f"{artifact.run_id}.json"
    target.write_bytes(
        artifact_json_bytes(artifact).replace(b'"schema_version": 1', b'"schema_version": 2')
    )

    with pytest.raises(UnsupportedArtifactVersionError):
        store.load(artifact.run_id)


def test_atomic_write_failure_preserves_previous_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    original = _artifact(updated_at=NOW)
    replacement = original.model_copy(update={"updated_at": NOW + timedelta(seconds=1)})
    store.save(original)

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("customer_signal.runtime.artifact_store.os.replace", fail_replace)
    with pytest.raises(ArtifactWriteError, match="atomic"):
        store.save(replacement)

    assert store.load(original.run_id) == original
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("raw_fields", {"email": "person@example.test"}),
        ("provider_response", "private model response"),
        ("api_key", SecretStr("must-not-persist")),
        ("identity_value", "unmasked-identity"),
    ],
)
def test_store_recursively_rejects_nonpublic_data(tmp_path: Path, key: str, value: object) -> None:
    artifact_directory = tmp_path / "artifacts"
    store = ArtifactStore(artifact_directory)
    forged = _artifact().model_copy(update={key: value})

    with pytest.raises(UnsafeArtifactDataError):
        store.save(forged)

    assert not artifact_directory.exists()


def test_artifact_enforces_lifecycle_and_failed_step_membership() -> None:
    values = _artifact(status="running").model_dump()
    values["status"] = "completed"
    with pytest.raises(ValidationError, match="completed_at"):
        RunArtifact.model_validate(values)

    values = _artifact(status="failed").model_dump()
    values["error"] = None
    with pytest.raises(ValidationError, match="error"):
        RunArtifact.model_validate(values)

    values = _artifact(status="failed").model_dump()
    values["plan"] = _plan()
    values["failed_step_id"] = "step-forged"
    with pytest.raises(ValidationError, match="failed_step_id"):
        RunArtifact.model_validate(values)
