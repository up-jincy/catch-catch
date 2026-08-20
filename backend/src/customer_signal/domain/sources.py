"""Source manifests, event scopes, and safe public source descriptions."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from customer_signal.domain.models import CustomerEvent, DomainModel
from customer_signal.domain.types import GenericPrimitiveName, SourceId


type PiiClassification = Literal["none", "direct", "quasi", "sensitive"]
type IdentityQualityLevel = Literal["exact", "declared", "synthetic", "unknown"]


class TimeRange(DomainModel):
    """A timezone-aware half-open interval."""

    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        return self


class EventScope(TimeRange):
    """Bounded source and time selection supplied to an adapter."""

    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    max_events: int = Field(default=1_000, ge=1, le=10_000)

    @field_validator("source_ids")
    @classmethod
    def validate_unique_source_ids(cls, value: list[SourceId]) -> list[SourceId]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must be unique")
        return value


class FieldDescriptor(DomainModel):
    """Internal semantic-to-source field declaration."""

    name: str = Field(min_length=1, max_length=128)
    semantic_type: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1_000)
    unit: str | None = Field(default=None, max_length=64)
    pii_classification: PiiClassification
    source_field: str | None = Field(default=None, min_length=1, max_length=256)


class PublicFieldDescriptor(DomainModel):
    """A semantic descriptor that cannot disclose source column or PII metadata."""

    name: str
    semantic_type: str
    description: str
    unit: str | None = None

    @classmethod
    def from_internal(cls, descriptor: FieldDescriptor) -> Self:
        return cls(
            name=descriptor.name,
            semantic_type=descriptor.semantic_type,
            description=descriptor.description,
            unit=descriptor.unit,
        )


class RefreshDescriptor(DomainModel):
    cadence: str = Field(min_length=1, max_length=128)
    max_lag_minutes: int = Field(default=1_440, ge=0, le=525_600)


class MaskingPolicy(DomainModel):
    field_masks: dict[str, str] = Field(default_factory=dict)


class IdentityQuality(DomainModel):
    level: IdentityQualityLevel
    description: str = Field(min_length=1, max_length=1_000)
    namespace: str = Field(min_length=1, max_length=128)


class SourceManifest(DomainModel):
    """Internal adapter contract for one source."""

    source_id: SourceId
    label: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=2_000)
    adapter_version: str = Field(min_length=1, max_length=64)
    manifest_version: str = Field(min_length=1, max_length=64)
    data_interval: TimeRange
    refresh_cadence: RefreshDescriptor
    supported_event_types: list[str] = Field(min_length=1, max_length=128)
    supported_topics: list[str] = Field(min_length=1, max_length=512)
    supported_outcomes: list[str] = Field(min_length=1, max_length=512)
    dimensions: list[FieldDescriptor] = Field(default_factory=list, max_length=256)
    measures: list[FieldDescriptor] = Field(default_factory=list, max_length=256)
    generic_capabilities: list[GenericPrimitiveName] = Field(min_length=1, max_length=10)
    masking_policy: MaskingPolicy
    identity_quality: IdentityQuality

    @field_validator(
        "supported_event_types",
        "supported_topics",
        "supported_outcomes",
        "generic_capabilities",
    )
    @classmethod
    def validate_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("manifest semantic values must be unique")
        return value

    @model_validator(mode="after")
    def validate_declared_field_names(self) -> Self:
        names = [field.name for field in [*self.dimensions, *self.measures]]
        if len(names) != len(set(names)):
            raise ValueError("dimension and measure names must be unique")
        return self

    def validate_event(self, event: CustomerEvent) -> None:
        """Reject event data outside this source's declared semantic contract."""

        if event.source_id != self.source_id:
            raise ValueError("event source_id does not match manifest")
        if event.event_type not in self.supported_event_types:
            raise ValueError("event_type is not supported by manifest")
        if event.topic not in self.supported_topics:
            raise ValueError("topic is not supported by manifest")
        if event.outcome not in self.supported_outcomes:
            raise ValueError("outcome is not supported by manifest")

        dimension_names = {descriptor.name for descriptor in self.dimensions}
        measure_names = {descriptor.name for descriptor in self.measures}
        undeclared_dimensions = set(event.dimensions) - dimension_names
        undeclared_measures = set(event.measures) - measure_names
        if undeclared_dimensions:
            raise ValueError("event contains undeclared dimension")
        if undeclared_measures:
            raise ValueError("event contains undeclared measure")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in event.measures.values()):
            raise ValueError("event measures must be finite numbers")
        if any(value != value or value in (float("inf"), float("-inf")) for value in event.measures.values()):
            raise ValueError("event measures must be finite")


class PublicSourceManifest(DomainModel):
    """Safe source metadata intended for planners and public-facing APIs."""

    source_id: SourceId
    label: str
    description: str
    manifest_version: str
    data_interval: TimeRange
    refresh_cadence: RefreshDescriptor
    supported_event_types: list[str]
    supported_topics: list[str]
    supported_outcomes: list[str]
    dimensions: list[PublicFieldDescriptor]
    measures: list[PublicFieldDescriptor]
    generic_capabilities: list[GenericPrimitiveName]
    identity_quality: IdentityQualityLevel

    @classmethod
    def from_manifest(cls, manifest: SourceManifest) -> Self:
        def safe_fields(descriptors: list[FieldDescriptor]) -> list[PublicFieldDescriptor]:
            return [
                PublicFieldDescriptor.from_internal(descriptor)
                for descriptor in descriptors
                if descriptor.pii_classification == "none"
            ]

        return cls(
            source_id=manifest.source_id,
            label=manifest.label,
            description=manifest.description,
            manifest_version=manifest.manifest_version,
            data_interval=manifest.data_interval,
            refresh_cadence=manifest.refresh_cadence,
            supported_event_types=manifest.supported_event_types,
            supported_topics=manifest.supported_topics,
            supported_outcomes=manifest.supported_outcomes,
            dimensions=safe_fields(manifest.dimensions),
            measures=safe_fields(manifest.measures),
            generic_capabilities=manifest.generic_capabilities,
            identity_quality=manifest.identity_quality.level,
        )


class PublicSourceList(DomainModel):
    sources: list[PublicSourceManifest] = Field(default_factory=list, max_length=32)


__all__ = [
    "EventScope",
    "FieldDescriptor",
    "IdentityQuality",
    "MaskingPolicy",
    "PublicFieldDescriptor",
    "PublicSourceList",
    "PublicSourceManifest",
    "RefreshDescriptor",
    "SourceManifest",
    "TimeRange",
]
