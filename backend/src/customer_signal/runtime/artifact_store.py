"""Atomic filesystem persistence for public Run Artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

from pydantic import ValidationError

from customer_signal.runtime.artifacts import (
    ArtifactSummary,
    RunArtifact,
    UnsafeArtifactDataError,
    artifact_json_bytes,
    validate_public_artifact_data,
)

if TYPE_CHECKING:
    from customer_signal.config import Settings


class ArtifactStoreError(RuntimeError):
    """Base class for functional Artifact persistence failures."""


class InvalidRunIdError(ArtifactStoreError, ValueError):
    """Raised before a non-UUID value can participate in path construction."""


class ArtifactNotFoundError(ArtifactStoreError, FileNotFoundError):
    """Raised when a UUID-safe Artifact path does not exist."""


class UnsupportedArtifactVersionError(ArtifactStoreError):
    """Raised when persisted JSON declares a schema this server cannot read."""


class CorruptArtifactError(ArtifactStoreError):
    """Raised when an Artifact file cannot satisfy the schema-v1 contract."""


class ArtifactWriteError(ArtifactStoreError):
    """Raised after a failed atomic write while preserving any previous file."""


class ArtifactStore:
    """Store one canonical JSON file per UUID without writing during construction."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.artifact_directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def save(self, artifact: RunArtifact) -> None:
        validate_public_artifact_data(artifact)
        payload = artifact_json_bytes(artifact)
        validated = _validate_payload(payload)
        target = self._path(validated.run_id)
        temp = target.with_suffix(f".{uuid4().hex}.tmp")

        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with temp.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        except OSError as error:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ArtifactWriteError("Artifact atomic save failed") from error

    def load(self, run_id: UUID | str) -> RunArtifact:
        target = self._path(run_id)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactNotFoundError("Run Artifact not found") from error
        except OSError as error:
            raise ArtifactStoreError("Run Artifact could not be read") from error
        return _validate_payload(payload)

    def load_bytes(self, run_id: UUID | str) -> bytes:
        return artifact_json_bytes(self.load(run_id))

    def list(self) -> list[RunArtifact]:
        if not self._directory.exists():
            return []
        artifacts: list[RunArtifact] = []
        for path in self._directory.glob("*.json"):
            try:
                run_id = _parse_run_id(path.stem)
            except InvalidRunIdError:
                continue
            artifacts.append(self.load(run_id))
        return sorted(
            artifacts,
            key=lambda artifact: (artifact.updated_at, str(artifact.run_id)),
            reverse=True,
        )

    def list_artifacts(self) -> list[RunArtifact]:
        """Explicit alias used by dependency wiring and API code."""

        return self.list()

    def list_summaries(self) -> list[ArtifactSummary]:
        return [ArtifactSummary.from_artifact(artifact) for artifact in self.list()]

    def _path(self, run_id: UUID | str) -> Path:
        parsed = _parse_run_id(run_id)
        return self._directory / f"{parsed}.json"


def _parse_run_id(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise InvalidRunIdError("run_id must be a UUID")
    try:
        return UUID(value)
    except (AttributeError, ValueError) as error:
        raise InvalidRunIdError("run_id must be a UUID") from error


def _validate_payload(payload: bytes) -> RunArtifact:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CorruptArtifactError("Run Artifact is not valid UTF-8 JSON") from error
    if not isinstance(decoded, dict):
        raise CorruptArtifactError("Run Artifact JSON must be an object")
    schema_version = decoded.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        if schema_version is not None:
            raise UnsupportedArtifactVersionError(
                f"unsupported Run Artifact schema_version: {schema_version!r}"
            )
        raise CorruptArtifactError("Run Artifact schema_version is missing")
    try:
        artifact = RunArtifact.model_validate_json(payload)
    except ValidationError as error:
        raise CorruptArtifactError("Run Artifact violates schema version 1") from error
    validate_public_artifact_data(artifact)
    return artifact


__all__ = [
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactWriteError",
    "CorruptArtifactError",
    "InvalidRunIdError",
    "UnsafeArtifactDataError",
    "UnsupportedArtifactVersionError",
]
