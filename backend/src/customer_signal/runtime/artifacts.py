"""Versioned, public-safe persistence contracts for analysis Run history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from customer_signal.agent.contracts import RunRequest
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisNote,
    AnalysisPlan,
    PublicRunError,
    RunStatus,
)
from customer_signal.domain.facts import AnalysisFact
from customer_signal.domain.reports import (
    CustomerSignalReport,
    GenericOrLegacyReport,
)
from customer_signal.domain.types import SourceId


class UnsafeArtifactDataError(ValueError):
    """Raised when a persistence candidate contains non-public or provider-private data."""


class ArtifactContractModel(BaseModel):
    """Strict base for values written to the versioned Artifact JSON boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ClarificationRecord(ArtifactContractModel):
    """One public clarification prompt and its optional user answer."""

    kind: Literal["clarification"] = "clarification"
    clarification_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=1_000)
    requested_at: AwareDatetime | None = None
    answered_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def bind_answer_time(self) -> Self:
        if self.answered_at is not None and self.answer is None:
            raise ValueError("answered_at requires a clarification answer")
        if (
            self.requested_at is not None
            and self.answered_at is not None
            and self.answered_at < self.requested_at
        ):
            raise ValueError("clarification answered_at cannot precede requested_at")
        return self


class RunVersions(ArtifactContractModel):
    """Dataset and execution versions needed to explain or reproduce a Run."""

    dataset_versions: list[str] = Field(default_factory=list, max_length=32)
    adapter_versions: dict[SourceId, str] = Field(default_factory=dict, max_length=32)
    manifest_versions: dict[SourceId, str] = Field(default_factory=dict, max_length=32)
    prompt_version: str | None = Field(default=None, max_length=128)
    model_version: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_stable_nonblank_versions(self) -> Self:
        if len(self.dataset_versions) != len(set(self.dataset_versions)):
            raise ValueError("dataset_versions must be unique")
        values = [
            *self.dataset_versions,
            *self.adapter_versions.values(),
            *self.manifest_versions.values(),
        ]
        if self.prompt_version is not None:
            values.append(self.prompt_version)
        if self.model_version is not None:
            values.append(self.model_version)
        if any(not value.strip() for value in values):
            raise ValueError("Run versions must be nonblank")
        if set(self.adapter_versions) != set(self.manifest_versions):
            raise ValueError("adapter and manifest versions must cover the same sources")
        return self


class RunArtifact(ArtifactContractModel):
    """Schema-v1 JSON source of truth for a complete or partial analysis Run."""

    schema_version: Literal[1] = 1
    run_id: UUID
    status: RunStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    request: RunRequest
    goal: AnalysisGoal | None = None
    clarification: ClarificationRecord | None = None
    plan: AnalysisPlan | None = None
    plan_history: list[AnalysisPlan] = Field(default_factory=list, max_length=32)
    facts: list[AnalysisFact] = Field(default_factory=list, max_length=128)
    notes: list[AnalysisNote] = Field(default_factory=list, max_length=128)
    report: GenericOrLegacyReport | None = None
    last_event_id: int = Field(default=0, ge=0)
    versions: RunVersions
    failed_step_id: str | None = Field(default=None, max_length=128)
    limitations: list[str] = Field(default_factory=list, max_length=64)
    error: PublicRunError | None = None

    @field_validator("limitations")
    @classmethod
    def require_public_limitations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Artifact limitations must be unique")
        if any(not limitation.strip() or len(limitation) > 500 for limitation in value):
            raise ValueError("Artifact limitations must be bounded and nonblank")
        return value

    @model_validator(mode="after")
    def require_consistent_lifecycle(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        terminal = self.status in {"completed", "degraded", "failed"}
        if terminal and self.completed_at is None:
            raise ValueError("terminal Artifact requires completed_at")
        if not terminal and self.completed_at is not None:
            raise ValueError("nonterminal Artifact cannot have completed_at")
        if self.completed_at is not None and not (
            self.created_at <= self.completed_at <= self.updated_at
        ):
            raise ValueError("completed_at must remain within the Run timestamps")
        if self.status == "awaiting_clarification" and self.clarification is None:
            raise ValueError("awaiting_clarification requires a clarification record")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed Artifact requires a safe public error")

        fact_ids = [fact.fact_id for fact in self.facts]
        note_ids = [note.note_id for note in self.notes]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Artifact fact_id values must be unique")
        if len(note_ids) != len(set(note_ids)):
            raise ValueError("Artifact note_id values must be unique")
        if any(not set(note.fact_ids) <= set(fact_ids) for note in self.notes):
            raise ValueError("Artifact Notes may reference only persisted Facts")

        if (
            self.plan is not None
            and self.goal is not None
            and self.plan.goal_id != self.goal.goal_id
        ):
            raise ValueError("Artifact Plan goal_id must equal the persisted Goal")
        if self.plan_history:
            revisions = [plan.revision for plan in self.plan_history]
            if any(current >= following for current, following in zip(revisions, revisions[1:])):
                raise ValueError("Artifact Plan history revisions must be unique and increasing")
            if self.plan != self.plan_history[-1]:
                raise ValueError("current Artifact Plan must equal the last Plan history revision")
        if self.failed_step_id is not None:
            if self.plan is None or self.failed_step_id not in {
                step.step_id for step in self.plan.steps
            }:
                raise ValueError("failed_step_id must belong to the current Artifact Plan")
            if self.error is not None and self.error.step_id not in {None, self.failed_step_id}:
                raise ValueError("failed_step_id must equal the public error step_id")
        if isinstance(self.report, CustomerSignalReport) and self.goal is not None:
            if self.report.goal.goal_id != self.goal.goal_id:
                raise ValueError("generic report Goal must equal the Artifact Goal")

        validate_public_artifact_data(self)
        return self


class ArtifactDocumentScope(ArtifactContractModel):
    start_at: AwareDatetime
    end_at: AwareDatetime
    source_ids: list[SourceId]


class ArtifactDocumentProvenance(ArtifactContractModel):
    fact_ids: list[str]
    result_ids: list[str]
    source_ids: list[SourceId]
    evidence_ids: list[str]
    dataset_versions: list[str]
    adapter_versions: dict[SourceId, str]
    manifest_versions: dict[SourceId, str]
    prompt_version: str | None = None
    model_version: str | None = None
    last_event_id: int = Field(ge=0)


class ArtifactDocument(ArtifactContractModel):
    """Read model containing only sections derived from one persisted Artifact."""

    document_kind: Literal["run_artifact"] = "run_artifact"
    run_id: UUID
    status: RunStatus
    headline: str
    question: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    scope: ArtifactDocumentScope
    goal: AnalysisGoal | None = None
    clarification: ClarificationRecord | None = None
    plan: AnalysisPlan | None = None
    plan_history: list[AnalysisPlan] = Field(default_factory=list, max_length=32)
    facts: list[AnalysisFact] = Field(default_factory=list, max_length=128)
    notes: list[AnalysisNote]
    report: GenericOrLegacyReport | None = None
    provenance: ArtifactDocumentProvenance
    limitations: list[str]
    error: PublicRunError | None = None


class ArtifactSummary(ArtifactContractModel):
    """Small newest-first history row for Run selection UIs."""

    run_id: UUID
    status: RunStatus
    question: str
    headline: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    error_code: str | None = None

    @classmethod
    def from_artifact(cls, artifact: RunArtifact) -> Self:
        headline = artifact.request.question
        if artifact.clarification is not None:
            headline = artifact.clarification.question
        if artifact.goal is not None:
            headline = artifact.goal.objective
        if artifact.report is not None:
            headline = artifact.report.headline
        return cls(
            run_id=artifact.run_id,
            status=artifact.status,
            question=artifact.request.question,
            headline=headline,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
            completed_at=artifact.completed_at,
            error_code=artifact.error.code if artifact.error is not None else None,
        )


class ArtifactListResponse(ArtifactContractModel):
    artifacts: list[ArtifactSummary]


_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "chain_of_thought",
        "cookie",
        "email",
        "identity",
        "identity_value",
        "identities",
        "internal_text",
        "model_thoughts",
        "password",
        "phone",
        "provider_message",
        "provider_request",
        "provider_response",
        "provider_text",
        "raw_fields",
        "reasoning",
        "secret",
        "thoughts",
        "token",
    }
)
_FORBIDDEN_SUFFIXES = ("_api_key", "_password", "_secret", "_token")


def validate_public_artifact_data(value: object, *, path: str = "artifact") -> None:
    """Recursively fail closed on known private/provider/raw persistence surfaces."""

    if isinstance(value, SecretStr):
        raise UnsafeArtifactDataError(f"{path} contains a SecretStr")
    if isinstance(value, BaseModel):
        for key, nested in value.__dict__.items():
            _validate_public_key(key, path)
            validate_public_artifact_data(nested, path=f"{path}.{key}")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key)
            _validate_public_key(normalized, path)
            validate_public_artifact_data(nested, path=f"{path}.{normalized}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            validate_public_artifact_data(nested, path=f"{path}[{index}]")


def _validate_public_key(key: str, path: str) -> None:
    normalized = key.strip().lower().replace("-", "_")
    if (
        normalized in _FORBIDDEN_EXACT_KEYS
        or normalized.startswith("raw_")
        or normalized.endswith(_FORBIDDEN_SUFFIXES)
    ):
        raise UnsafeArtifactDataError(f"{path}.{key} is not public Artifact data")


def artifact_json_bytes(artifact: RunArtifact) -> bytes:
    """Return canonical UTF-8 JSON bytes suitable for persistence or download."""

    validate_public_artifact_data(artifact)
    return f"{artifact.model_dump_json(indent=2)}\n".encode()


__all__ = [
    "ArtifactContractModel",
    "ArtifactDocument",
    "ArtifactDocumentProvenance",
    "ArtifactDocumentScope",
    "ArtifactListResponse",
    "ArtifactSummary",
    "ClarificationRecord",
    "RunArtifact",
    "RunVersions",
    "UnsafeArtifactDataError",
    "artifact_json_bytes",
    "validate_public_artifact_data",
]
