"""Contracts every Analysis Pack implements.

A Pack owns one analysis: its input contract, artifact schemas, execution, and
public emissions.  It never touches the EventJournal, sequences, or event IDs;
the Pack Kernel validates emissions and commits Canonical Run Events.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from customer_signal.domain.analysis import PublicRunError
from customer_signal.journal.events import PackRef, assert_public_payload

type ArtifactKind = Literal["goal", "plan", "fact", "note", "report"]

_SCHEMA_ID_PATTERN = r"^[a-z][a-z0-9_.-]{1,127}$"
_CATALOG_KEY_PATTERN = r"^[A-Z][A-Za-z0-9]{0,63}$"


class PackContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def schema_digest(model: type[BaseModel]) -> str:
    """Stable digest of a model's JSON schema, pinned per Run for replay."""

    schema = model.model_json_schema()
    canonical = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class ArtifactSchema(PackContract):
    """One versioned public artifact kind a Pack may commit."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    kind: ArtifactKind
    schema_id: str = Field(pattern=_SCHEMA_ID_PATTERN)
    model: type[BaseModel]

    @property
    def digest(self) -> str:
        return schema_digest(self.model)


class AnalysisPackSpec(PackContract):
    """Public identity and contract surface of one Analysis Pack."""

    pack_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    pack_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title_ko: str = Field(min_length=1, max_length=200)
    description_ko: str = Field(min_length=1, max_length=1_000)
    input_schema_id: str = Field(pattern=_SCHEMA_ID_PATTERN)
    artifact_schemas: tuple[ArtifactSchema, ...] = Field(min_length=1)
    required_catalog_keys: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_schema_uniqueness(self) -> Self:
        kinds = [schema.kind for schema in self.artifact_schemas]
        if len(kinds) != len(set(kinds)):
            raise ValueError("artifact schema kinds must be unique within a Pack")
        schema_ids = [schema.schema_id for schema in self.artifact_schemas]
        if len(schema_ids) != len(set(schema_ids)):
            raise ValueError("artifact schema_id values must be unique within a Pack")
        for key in self.required_catalog_keys:
            if not re.match(_CATALOG_KEY_PATTERN, key):
                raise ValueError(f"required catalog key format is invalid: {key}")
        if len(self.required_catalog_keys) != len(set(self.required_catalog_keys)):
            raise ValueError("required_catalog_keys must be unique")
        return self

    def schema_for(self, kind: ArtifactKind) -> ArtifactSchema:
        for schema in self.artifact_schemas:
            if schema.kind == kind:
                return schema
        raise KeyError(f"Pack declares no artifact schema for kind: {kind}")

    @property
    def contract_digest(self) -> str:
        payload = {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "input_schema_id": self.input_schema_id,
            "artifacts": [
                [schema.kind, schema.schema_id, schema.digest]
                for schema in self.artifact_schemas
            ],
            "required_catalog_keys": list(self.required_catalog_keys),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    @property
    def ref(self) -> PackRef:
        return PackRef(
            pack_id=self.pack_id,
            pack_version=self.pack_version,
            contract_digest=self.contract_digest,
        )


class PackContext(PackContract):
    """Run-scoped execution context handed to a Pack by the Kernel."""

    run_id: UUID
    options: dict[str, JsonValue] = Field(default_factory=dict)
    resumed: bool = False


class GoalDraft(PackContract):
    emission: Literal["goal"] = "goal"
    value: dict[str, JsonValue]


class PlanDraft(PackContract):
    emission: Literal["plan"] = "plan"
    value: dict[str, JsonValue]
    revised: bool = False


class FactDraft(PackContract):
    emission: Literal["fact"] = "fact"
    value: dict[str, JsonValue]
    step_id: str | None = Field(default=None, max_length=128)


class NoteDraft(PackContract):
    emission: Literal["note"] = "note"
    value: dict[str, JsonValue]


class ReportDraft(PackContract):
    emission: Literal["report"] = "report"
    value: dict[str, JsonValue]
    meta: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_public_meta(self) -> Self:
        assert_public_payload(self.meta)
        return self


class ActivityDraft(PackContract):
    emission: Literal["activity"] = "activity"
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_public_activity(self) -> Self:
        assert_public_payload(self.payload)
        return self


class InteractionDraft(PackContract):
    emission: Literal["interaction"] = "interaction"
    phase: Literal["requested", "answered"]
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_public_interaction(self) -> Self:
        assert_public_payload(self.payload)
        return self


class OutcomeDraft(PackContract):
    """The Pack's declared domain outcome; the Kernel owns the lifecycle event."""

    emission: Literal["outcome"] = "outcome"
    status: Literal["completed", "degraded", "failed", "awaiting_input"]
    limitations: list[str] = Field(default_factory=list, max_length=32)
    error: PublicRunError | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> Self:
        if self.status == "failed" and self.error is None:
            raise ValueError("failed outcome requires a public error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("only failed outcomes may carry a public error")
        if self.status == "degraded" and not self.limitations:
            raise ValueError("degraded outcome requires at least one limitation")
        return self


type PackEmission = Annotated[
    GoalDraft
    | PlanDraft
    | FactDraft
    | NoteDraft
    | ReportDraft
    | ActivityDraft
    | InteractionDraft
    | OutcomeDraft,
    Field(discriminator="emission"),
]


class PackDomainError(Exception):
    """A safe, Pack-declared public failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        step_id: str | None = None,
        suggested_questions: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.public_error = PublicRunError(
            code=code,
            message=message,
            step_id=step_id,
            suggested_questions=list(suggested_questions),
        )


class PackDegraded(Exception):
    """A safe no-result end state with public limitations."""

    def __init__(self, limitations: tuple[str, ...]) -> None:
        super().__init__("analysis degraded")
        self.limitations = tuple(limitations)


@runtime_checkable
class AnalysisPackAdapter(Protocol):
    """One analysis as a deep module: input, execution, and public emissions."""

    spec: AnalysisPackSpec
    Input: type[BaseModel]

    def execute(
        self,
        request: BaseModel,
        context: PackContext,
    ) -> AsyncIterator[PackEmission]: ...


__all__ = [
    "ActivityDraft",
    "AnalysisPackAdapter",
    "AnalysisPackSpec",
    "ArtifactKind",
    "ArtifactSchema",
    "FactDraft",
    "GoalDraft",
    "InteractionDraft",
    "NoteDraft",
    "OutcomeDraft",
    "PackContext",
    "PackContract",
    "PackDegraded",
    "PackDomainError",
    "PackEmission",
    "PlanDraft",
    "ReportDraft",
    "schema_digest",
]
