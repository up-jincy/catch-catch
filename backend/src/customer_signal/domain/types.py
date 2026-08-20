"""Portable identifiers and primitive-name vocabularies."""

from typing import Annotated, Literal

from pydantic import StringConstraints


type SourceId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
]

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


__all__ = ["GenericPrimitiveName", "LegacySourceId", "PrimitiveName", "SourceId"]
