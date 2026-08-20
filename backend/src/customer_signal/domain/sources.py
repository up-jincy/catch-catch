"""Strict source manifests, adapter scopes, and public source projections."""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from customer_signal.domain.models import CanonicalCustomerEvent, DomainModel
from customer_signal.domain.types import GenericPrimitiveName, SourceId


type RefreshCadence = Literal["static_demo", "hourly", "daily", "weekly"]
type PiiClassification = Literal["none", "quasi_identifier", "direct_identifier"]
type DimensionSemanticType = Literal["category", "boolean", "identifier", "text"]
type MeasureSemanticType = Literal["integer", "number"]
type MaskingMode = Literal["hash", "partial", "redact"]
type IdentityLinkMethod = Literal["exact", "declared", "synthetic"]
type IdentityConfidence = Annotated[
    float,
    Field(strict=True, ge=0, le=1, allow_inf_nan=False),
]


class SourceContractModel(DomainModel):
    """Strict base for manifest values received at an adapter boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class TimeRange(SourceContractModel):
    """A timezone-aware half-open interval ``[start_at, end_at)``."""

    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def require_half_open_interval(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before exclusive end_at")
        return self


class EventScope(TimeRange):
    """Bounded source and time selection supplied to an adapter."""

    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    max_events: int = Field(default=1_000, ge=1, le=10_000)

    @field_validator("source_ids")
    @classmethod
    def require_unique_sources(cls, value: list[SourceId]) -> list[SourceId]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must be unique")
        return value


class DimensionDescriptor(SourceContractModel):
    """Semantic contract for one categorical, textual, or identity dimension."""

    semantic_type: DimensionSemanticType
    description: str = Field(min_length=1)
    pii_classification: PiiClassification
    allowed_values: frozenset[str] | None = None


class MeasureDescriptor(SourceContractModel):
    """Semantic contract for one finite numeric measure."""

    semantic_type: MeasureSemanticType
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    pii_classification: PiiClassification = "none"


class PublicDimensionDescriptor(SourceContractModel):
    """Safe projection of a non-PII dimension descriptor."""

    semantic_type: DimensionSemanticType
    description: str
    allowed_values: frozenset[str] | None = None

    @classmethod
    def from_internal(cls, descriptor: DimensionDescriptor) -> Self:
        return cls(
            semantic_type=descriptor.semantic_type,
            description=descriptor.description,
            allowed_values=descriptor.allowed_values,
        )


class PublicMeasureDescriptor(SourceContractModel):
    """Safe projection of a non-PII measure descriptor."""

    semantic_type: MeasureSemanticType
    description: str
    unit: str

    @classmethod
    def from_internal(cls, descriptor: MeasureDescriptor) -> Self:
        return cls(
            semantic_type=descriptor.semantic_type,
            description=descriptor.description,
            unit=descriptor.unit,
        )


class MaskingPolicy(SourceContractModel):
    """Internal masking mode by declared semantic field name."""

    rules: dict[str, MaskingMode] = Field(default_factory=dict)


class IdentityQualityDescriptor(SourceContractModel):
    """Declared namespace and provenance quality of source-native identities."""

    namespace: str = Field(min_length=1)
    link_method: IdentityLinkMethod
    confidence: IdentityConfidence


class SourceManifest(SourceContractModel):
    """Internal adapter contract for exactly one dynamic source."""

    source_id: SourceId
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    data_interval: TimeRange
    refresh_cadence: RefreshCadence
    supported_event_types: frozenset[str] = Field(min_length=1, max_length=128)
    supported_topics: frozenset[str] = Field(min_length=1, max_length=512)
    supported_outcomes: frozenset[str] = Field(min_length=1, max_length=512)
    dimensions: dict[str, DimensionDescriptor] = Field(default_factory=dict, max_length=256)
    measures: dict[str, MeasureDescriptor] = Field(default_factory=dict, max_length=256)
    capabilities: frozenset[GenericPrimitiveName] = Field(min_length=1, max_length=10)
    masking_policy: MaskingPolicy
    identity_quality: IdentityQualityDescriptor

    @model_validator(mode="after")
    def require_consistent_internal_field_policy(self) -> Self:
        field_names = set(self.dimensions) | set(self.measures)
        if set(self.dimensions) & set(self.measures):
            raise ValueError("dimension and measure names must be unique")
        unknown_masked_fields = set(self.masking_policy.rules) - field_names
        if unknown_masked_fields:
            raise ValueError("masking rules must reference declared fields")
        non_pii_masked_fields = {
            name
            for name in self.masking_policy.rules
            if self._field_pii_classification(name) == "none"
        }
        if non_pii_masked_fields:
            raise ValueError("masking rules must reference PII-classified fields")
        return self

    def _field_pii_classification(self, name: str) -> PiiClassification:
        descriptor = self.dimensions.get(name) or self.measures.get(name)
        assert descriptor is not None
        return descriptor.pii_classification

    def validate_event(self, event: CanonicalCustomerEvent) -> None:
        """Reject event values outside declared source semantics."""

        if event.source_id != self.source_id:
            raise ValueError("event source does not match manifest")
        if not self.data_interval.start_at <= event.occurred_at < self.data_interval.end_at:
            raise ValueError("event occurred_at is outside manifest data_interval")
        if event.event_type not in self.supported_event_types:
            raise ValueError("event_type is not supported by manifest")
        if event.topic not in self.supported_topics:
            raise ValueError("topic is not supported by manifest")
        if event.outcome not in self.supported_outcomes:
            raise ValueError("outcome is not supported by manifest")

        unknown_dimensions = set(event.dimensions) - set(self.dimensions)
        unknown_measures = set(event.measures) - set(self.measures)
        if unknown_dimensions:
            raise ValueError("undeclared dimension")
        if unknown_measures:
            raise ValueError("undeclared measure")

        for name, value in event.dimensions.items():
            descriptor = self.dimensions[name]
            if descriptor.allowed_values is not None and value not in descriptor.allowed_values:
                raise ValueError("dimension value is outside allowed values")
            if (
                descriptor.semantic_type == "boolean"
                and value is not None
                and type(value) is not bool
            ):
                raise ValueError("boolean dimension must contain a boolean value")

        for name, value in event.measures.items():
            descriptor = self.measures[name]
            if type(value) not in (int, float):
                raise ValueError("measure must contain a finite integer or number value")
            if type(value) is float and not isfinite(value):
                raise ValueError("measure must contain a finite integer or number value")
            if descriptor.semantic_type == "integer" and type(value) is not int:
                raise ValueError("integer measure must contain an integer value")

        if not event.identities:
            raise ValueError("event must include an identity in the manifest namespace")
        if any(
            identity.namespace != self.identity_quality.namespace for identity in event.identities
        ):
            raise ValueError("event identity namespace does not match manifest")


class PublicSourceManifest(SourceContractModel):
    """Public-safe manifest with no PII names, raw mappings, or identity namespace."""

    source_id: SourceId
    label: str
    description: str
    data_interval: TimeRange
    refresh_cadence: RefreshCadence
    supported_event_types: frozenset[str]
    supported_topics: frozenset[str]
    supported_outcomes: frozenset[str]
    dimensions: dict[str, PublicDimensionDescriptor]
    measures: dict[str, PublicMeasureDescriptor]
    capabilities: frozenset[GenericPrimitiveName]
    adapter_version: str
    manifest_version: str

    @classmethod
    def from_internal(cls, manifest: SourceManifest) -> Self:
        return cls(
            source_id=manifest.source_id,
            label=manifest.label,
            description=manifest.description,
            data_interval=manifest.data_interval,
            refresh_cadence=manifest.refresh_cadence,
            supported_event_types=manifest.supported_event_types,
            supported_topics=manifest.supported_topics,
            supported_outcomes=manifest.supported_outcomes,
            dimensions={
                name: PublicDimensionDescriptor.from_internal(descriptor)
                for name, descriptor in manifest.dimensions.items()
                if descriptor.pii_classification == "none"
            },
            measures={
                name: PublicMeasureDescriptor.from_internal(descriptor)
                for name, descriptor in manifest.measures.items()
                if descriptor.pii_classification == "none"
            },
            capabilities=manifest.capabilities,
            adapter_version=manifest.adapter_version,
            manifest_version=manifest.manifest_version,
        )


class PublicSourceList(SourceContractModel):
    items: list[PublicSourceManifest] = Field(default_factory=list, max_length=32)


__all__ = [
    "DimensionDescriptor",
    "EventScope",
    "IdentityQualityDescriptor",
    "MaskingPolicy",
    "MeasureDescriptor",
    "PublicDimensionDescriptor",
    "PublicMeasureDescriptor",
    "PublicSourceList",
    "PublicSourceManifest",
    "RefreshCadence",
    "SourceManifest",
    "TimeRange",
]
