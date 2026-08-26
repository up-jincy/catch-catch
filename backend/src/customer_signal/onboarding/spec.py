"""Declarative mapping spec turning one raw table into a canonical event source."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from customer_signal.domain.sources import (
    DimensionSemanticType,
    IdentityConfidence,
    IdentityLinkMethod,
    MaskingMode,
    MeasureSemanticType,
    PiiClassification,
)
from customer_signal.domain.types import SourceId


class OnboardingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldRule(OnboardingModel):
    """How one canonical event field is produced: a column, a constant, or a value map."""

    column: str | None = None
    const: str | None = None
    value_map: dict[str, str] | None = None
    default: str | None = None

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> Self:
        if (self.column is None) == (self.const is None):
            raise ValueError("field rule needs exactly one of column or const")
        if self.const is not None and (self.value_map is not None or self.default is not None):
            raise ValueError("value_map/default only apply to column rules")
        return self


class DimensionSpec(OnboardingModel):
    column: str = Field(min_length=1)
    semantic_type: DimensionSemanticType
    description: str = Field(min_length=1)
    pii_classification: PiiClassification = "none"
    masking: MaskingMode | None = None

    @model_validator(mode="after")
    def require_masking_for_pii(self) -> Self:
        if (self.pii_classification != "none") != (self.masking is not None):
            raise ValueError("PII dimensions need a masking mode; non-PII must not have one")
        return self


class MeasureSpec(OnboardingModel):
    column: str = Field(min_length=1)
    semantic_type: MeasureSemanticType
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class IdentitySpec(OnboardingModel):
    namespace: str = Field(min_length=1)
    customer_column: str = Field(min_length=1)
    link_method: IdentityLinkMethod = "exact"
    confidence: IdentityConfidence = 1.0


class SourceMappingSpec(OnboardingModel):
    """The complete, human-reviewable contract for onboarding one raw table."""

    source_id: SourceId
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    timestamp_column: str = Field(min_length=1)
    timezone: str = "UTC"
    event_type: FieldRule
    action: FieldRule
    topic: FieldRule
    outcome: FieldRule
    text: FieldRule = FieldRule(const="")
    identity: IdentitySpec
    dimensions: dict[str, DimensionSpec] = Field(default_factory=dict)
    measures: dict[str, MeasureSpec] = Field(default_factory=dict)
    status: Literal["draft", "approved"] = "draft"

    @model_validator(mode="after")
    def require_unique_field_names(self) -> Self:
        if set(self.dimensions) & set(self.measures):
            raise ValueError("dimension and measure names must be unique")
        return self

    def referenced_columns(self) -> set[str]:
        columns = {self.timestamp_column, self.identity.customer_column}
        for rule in (self.event_type, self.action, self.topic, self.outcome, self.text):
            if rule.column is not None:
                columns.add(rule.column)
        columns.update(spec.column for spec in self.dimensions.values())
        columns.update(spec.column for spec in self.measures.values())
        return columns


__all__ = [
    "DimensionSpec",
    "FieldRule",
    "IdentitySpec",
    "MeasureSpec",
    "SourceMappingSpec",
]
