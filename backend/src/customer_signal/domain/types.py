"""Portable identifiers and primitive-name vocabularies."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, StringConstraints


type SourceId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
]

# Semantic event values are deliberately stricter than legacy ``attributes``.
# They form the bounded generic-analysis surface exposed by source manifests.
type DimensionValue = StrictStr | StrictInt | StrictBool | None
type FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
type MeasureValue = StrictInt | FiniteNumber

# Legacy tool surfaces remain intentionally restricted until they are migrated
# to SourceRegistry-backed dynamic lookup.
type LegacySourceId = Literal[
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
]

type GenericPrimitiveName = Literal[
    "catalog_sources",
    "profile_events",
    "aggregate_events",
    "segment_customers",
    "detect_repetition",
    "match_sequence",
    "compare_segments",
    "rank_customers",
    "get_customer_journey",
    "get_evidence",
]

type PrimitiveName = GenericPrimitiveName | Literal["match_journey_pattern"]


__all__ = [
    "DimensionValue",
    "FiniteNumber",
    "GenericPrimitiveName",
    "LegacySourceId",
    "MeasureValue",
    "PrimitiveName",
    "SourceId",
]
