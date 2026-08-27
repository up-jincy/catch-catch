"""Single consolidated definition of the ten generic analysis primitives.

Every table that used to be duplicated across the codebase — dependency
arity, required canonical metric keys, planner prompt vocabulary, objective
labels, and capability sets — derives from ``PRIMITIVE_DEFINITIONS``, so
adding or changing a primitive touches exactly one place. The module
fail-fasts at import time when its definitions drift from the hand-written
``GenericPrimitiveName`` Literal or from the strict primitive input classes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import get_args

from pydantic import BaseModel

from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    CompareSegmentsInput,
    DetectRepetitionInput,
    GetCustomerJourneyInput,
    GetEvidenceInput,
    MatchSequenceInput,
    ProfileEventsInput,
    RankCustomersInput,
    SegmentCustomersInput,
)
from customer_signal.domain.types import GenericPrimitiveName


@dataclass(frozen=True, slots=True)
class PrimitiveDefinition:
    """Complete server-owned knowledge about one generic analysis primitive."""

    name: GenericPrimitiveName
    input_type: type[BaseModel]
    required_metric_keys: frozenset[str]
    dynamic_metric: bool
    dependency_arity: tuple[int, int]
    objective_en: str
    description_ko: str | None
    prompt_metric_keys: tuple[str, ...]


def _definition(
    name: GenericPrimitiveName,
    *,
    input_type: type[BaseModel],
    required_metric_keys: frozenset[str],
    dependency_arity: tuple[int, int],
    objective_en: str,
    description_ko: str | None = None,
    dynamic_metric: bool = False,
    prompt_metric_keys: tuple[str, ...] | None = None,
) -> PrimitiveDefinition:
    return PrimitiveDefinition(
        name=name,
        input_type=input_type,
        required_metric_keys=required_metric_keys,
        dynamic_metric=dynamic_metric,
        dependency_arity=dependency_arity,
        objective_en=objective_en,
        description_ko=description_ko,
        prompt_metric_keys=(
            tuple(sorted(required_metric_keys))
            if prompt_metric_keys is None
            else prompt_metric_keys
        ),
    )


_DEFINITIONS: tuple[PrimitiveDefinition, ...] = (
    _definition(
        "catalog_sources",
        input_type=CatalogSourcesInput,
        required_metric_keys=frozenset({"source_count"}),
        dependency_arity=(0, 0),
        objective_en="Confirm available customer-signal sources",
    ),
    _definition(
        "profile_events",
        input_type=ProfileEventsInput,
        required_metric_keys=frozenset({"customer_count", "event_count"}),
        dependency_arity=(0, 0),
        objective_en="Profile customer-event distributions and quality",
    ),
    _definition(
        "aggregate_events",
        input_type=AggregateEventsInput,
        required_metric_keys=frozenset(),
        dynamic_metric=True,
        prompt_metric_keys=("exactly one requested metric key",),
        dependency_arity=(0, 0),
        objective_en="Calculate the requested customer-event metric",
        description_ko=(
            "특정 이벤트·피드백 속성을 가진 고객 수/이벤트 수 집계 "
            "(예: 부정 피드백이 많은 Topic별 고객 수)"
        ),
    ),
    _definition(
        "segment_customers",
        input_type=SegmentCustomersInput,
        required_metric_keys=frozenset({"segment_customer_count"}),
        dependency_arity=(0, 0),
        objective_en="Identify the customer Segment matching verified conditions",
        description_ko="모집단을 명명된 cohort로 분할",
    ),
    _definition(
        "detect_repetition",
        input_type=DetectRepetitionInput,
        required_metric_keys=frozenset({"repeated_customer_count"}),
        dependency_arity=(0, 0),
        objective_en="Detect repeated customer behavior",
        description_ko="동일 행동의 반복 탐지",
    ),
    _definition(
        "match_sequence",
        input_type=MatchSequenceInput,
        required_metric_keys=frozenset({"matched_customer_count"}),
        dependency_arity=(0, 0),
        objective_en="Match the requested customer-event Sequence",
        description_ko=(
            "순서가 있는 행동 패턴 매칭 (A 후 B, 시간 제한 포함 — "
            "예: 반복 행동 뒤 상담 전환, 가입 시작 후 미완료)"
        ),
    ),
    _definition(
        "compare_segments",
        input_type=CompareSegmentsInput,
        required_metric_keys=frozenset(),
        dynamic_metric=True,
        prompt_metric_keys=("<parameters.metric_key>_delta",),
        dependency_arity=(2, 2),
        objective_en="Compare verified Segment metrics",
        description_ko="두 선행 단계 metric의 비교",
    ),
    _definition(
        "rank_customers",
        input_type=RankCustomersInput,
        required_metric_keys=frozenset({"ranked_customer_count"}),
        dependency_arity=(1, 4),
        objective_en="Rank customers using verified signals",
    ),
    _definition(
        "get_customer_journey",
        input_type=GetCustomerJourneyInput,
        required_metric_keys=frozenset({"journey_event_count"}),
        dependency_arity=(1, 1),
        objective_en="Review a representative customer Journey",
    ),
    _definition(
        "get_evidence",
        input_type=GetEvidenceInput,
        required_metric_keys=frozenset({"evidence_record_count"}),
        dependency_arity=(1, 1),
        objective_en="Retrieve masked evidence for verified results",
    ),
)

PRIMITIVE_DEFINITIONS: Mapping[GenericPrimitiveName, PrimitiveDefinition] = MappingProxyType(
    {definition.name: definition for definition in _DEFINITIONS}
)


def _require_catalog_sync() -> None:
    """Fail fast at import time when definitions drift from the vocabulary."""

    literal_names = get_args(GenericPrimitiveName.__value__)
    if tuple(PRIMITIVE_DEFINITIONS) != literal_names:
        raise RuntimeError(
            "primitive catalog definitions must exactly match the "
            "GenericPrimitiveName Literal in order and content"
        )
    for name, definition in PRIMITIVE_DEFINITIONS.items():
        input_literal = get_args(definition.input_type.model_fields["primitive"].annotation)
        if input_literal != (name,):
            raise RuntimeError(f"primitive catalog input type for {name} declares {input_literal}")
        if definition.dynamic_metric != (not definition.required_metric_keys):
            raise RuntimeError(f"primitive catalog {name} dynamic_metric flag is inconsistent")
        if not definition.dynamic_metric and definition.prompt_metric_keys != tuple(
            sorted(definition.required_metric_keys)
        ):
            raise RuntimeError(f"primitive catalog {name} prompt metric keys are inconsistent")
        minimum, maximum = definition.dependency_arity
        if not 0 <= minimum <= maximum:
            raise RuntimeError(f"primitive catalog {name} dependency arity bounds are invalid")


_require_catalog_sync()


def primitive_names() -> tuple[GenericPrimitiveName, ...]:
    """All generic primitive names in canonical (Literal) order."""

    return tuple(PRIMITIVE_DEFINITIONS)


def dependency_arity_table() -> dict[GenericPrimitiveName, tuple[int, int]]:
    """(minimum, maximum) input-step arity for every primitive."""

    return {
        name: definition.dependency_arity for name, definition in PRIMITIVE_DEFINITIONS.items()
    }


def required_metric_keys_table() -> dict[GenericPrimitiveName, list[str]]:
    """Prompt-facing required metric keys, with placeholders for dynamic metrics."""

    return {
        name: list(definition.prompt_metric_keys)
        for name, definition in PRIMITIVE_DEFINITIONS.items()
    }


def required_canonical_metrics() -> dict[GenericPrimitiveName, frozenset[str]]:
    """Canonical metric keys every payload of a primitive must publish."""

    return {
        name: definition.required_metric_keys
        for name, definition in PRIMITIVE_DEFINITIONS.items()
    }


def descriptions_ko() -> dict[GenericPrimitiveName, str]:
    """Korean planner-guide descriptions for primitives that document one."""

    return {
        name: definition.description_ko
        for name, definition in PRIMITIVE_DEFINITIONS.items()
        if definition.description_ko is not None
    }


def objectives() -> dict[GenericPrimitiveName, str]:
    """English objective labels used for published analysis notes."""

    return {name: definition.objective_en for name, definition in PRIMITIVE_DEFINITIONS.items()}


def all_capabilities() -> frozenset[GenericPrimitiveName]:
    """The full capability set a source manifest can declare."""

    return frozenset(PRIMITIVE_DEFINITIONS)


def contract_document() -> dict[str, object]:
    """Machine-readable primitive contract shared with non-Python consumers."""

    return {
        "schema_version": 1,
        "description": (
            "Consolidated catalog of the ten generic analysis primitives. "
            "Generated from customer_signal.domain.primitive_catalog; edit "
            "that module and regenerate instead of editing this file."
        ),
        "primitives": [
            {
                "name": definition.name,
                "dependency_arity": {
                    "minimum": definition.dependency_arity[0],
                    "maximum": definition.dependency_arity[1],
                },
                "required_metric_keys": sorted(definition.required_metric_keys),
                "dynamic_metric": definition.dynamic_metric,
                "description_ko": definition.description_ko,
                "objective_en": definition.objective_en,
            }
            for definition in PRIMITIVE_DEFINITIONS.values()
        ],
    }


def render_contract_json() -> str:
    """Exact canonical text of contracts/primitive-catalog.json."""

    return json.dumps(contract_document(), ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "PRIMITIVE_DEFINITIONS",
    "PrimitiveDefinition",
    "all_capabilities",
    "contract_document",
    "dependency_arity_table",
    "descriptions_ko",
    "objectives",
    "primitive_names",
    "render_contract_json",
    "required_canonical_metrics",
    "required_metric_keys_table",
]
