"""Canonical Run Event envelope shared by every Analysis Pack.

The envelope never changes when a Pack is added.  Pack-specific Goal, Plan,
Fact, and Report values travel as ``artifact.committed`` events whose
``VersionedValue`` payload only the owning Pack and its Projector interpret.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

type CoreEventKind = Literal[
    "run.opened",
    "artifact.committed",
    "activity.changed",
    "interaction.changed",
    "run.awaiting_input",
    "run.resumed",
    "run.completed",
    "run.degraded",
    "run.failed",
]

TERMINAL_EVENT_KINDS: frozenset[str] = frozenset(
    {"run.completed", "run.degraded", "run.failed"}
)

# Matches the generic analysis publication gate (agent contracts): keys that
# may never appear in a public payload.  Masked public fields such as
# ``records`` and ``masked_customer_id`` are legitimate generic Fact content.
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "chain_of_thought",
        "internal_reasoning",
        "messages",
        "prompt",
        "provider_response",
        "raw_fields",
        "reasoning",
        "thoughts",
    }
)


def assert_public_payload(value: JsonValue) -> None:
    """Reject payload keys that must never leave the analysis boundary."""

    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_").replace(" ", "_")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"event payload key is not public: {key}")
            assert_public_payload(item)
    elif isinstance(value, list):
        for item in value:
            assert_public_payload(item)


class JournalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PackRef(JournalContract):
    """Exact Pack identity pinned for the lifetime of one Run."""

    pack_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    pack_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    contract_digest: str = Field(min_length=8, max_length=128)


class VersionedValue(JournalContract):
    """A validated, versioned public Artifact value."""

    schema_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    schema_digest: str = Field(min_length=8, max_length=128)
    value: JsonValue

    @model_validator(mode="after")
    def validate_public_value(self) -> Self:
        assert_public_payload(self.value)
        return self


class EventDraft(JournalContract):
    """What a producer proposes; the journal assigns identity at commit."""

    kind: CoreEventKind
    pack: PackRef
    artifact: VersionedValue | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_artifact_binding(self) -> Self:
        if self.kind == "artifact.committed" and self.artifact is None:
            raise ValueError("artifact.committed requires an artifact value")
        if self.kind != "artifact.committed" and self.artifact is not None:
            raise ValueError("only artifact.committed events may carry an artifact")
        assert_public_payload(self.payload)
        return self


class CanonicalRunEvent(JournalContract):
    """One committed, immutable event in a Run-local contiguous sequence."""

    schema_version: Literal[1] = 1
    event_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime
    pack: PackRef
    kind: CoreEventKind
    artifact: VersionedValue | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    causation_id: UUID
    correlation_id: UUID

    @model_validator(mode="after")
    def validate_artifact_binding(self) -> Self:
        if self.kind == "artifact.committed" and self.artifact is None:
            raise ValueError("artifact.committed requires an artifact value")
        if self.kind != "artifact.committed" and self.artifact is not None:
            raise ValueError("only artifact.committed events may carry an artifact")
        assert_public_payload(self.payload)
        return self

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_EVENT_KINDS


__all__ = [
    "CanonicalRunEvent",
    "CoreEventKind",
    "EventDraft",
    "PackRef",
    "TERMINAL_EVENT_KINDS",
    "VersionedValue",
    "assert_public_payload",
]
