"""Shared bounded contracts and semantic helpers for generic primitives."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol, TypeVar

from pydantic import BaseModel

from customer_signal.data.source_registry import SourceRegistry
from customer_signal.domain.facts import (
    AnalysisFact,
    AnalysisMetricFact,
    FactPayloadBase,
    FactProvenance,
    FieldRef,
)
from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.sources import EventScope, SourceManifest
from customer_signal.domain.types import DimensionValue, FiniteNumber


class PrimitiveExecutionError(RuntimeError):
    """Base error for a generic primitive that cannot safely publish a Fact."""

    code = "primitive_execution_error"


class PrimitiveLimitError(PrimitiveExecutionError):
    code = "primitive_limit_exceeded"


class PrimitiveTimeoutError(PrimitiveExecutionError):
    code = "primitive_timeout"


class PrimitiveCancelledError(PrimitiveExecutionError):
    code = "primitive_cancelled"


class PrimitiveContractError(PrimitiveExecutionError):
    code = "primitive_contract_error"


class PrimitiveDependencyError(PrimitiveContractError):
    code = "primitive_dependency_error"


class PrimitiveScopeError(PrimitiveContractError):
    code = "primitive_scope_error"


class NoDataScope(PrimitiveExecutionError):
    """The authorized source/time scope contains no canonical events."""

    code = "no_data_scope"

    def __init__(
        self, provenance: FactProvenance, message: str = "scope contains no events"
    ) -> None:
        super().__init__(message)
        self.provenance = provenance


class Budget(Protocol):
    def checkpoint(self) -> None: ...


PrimitiveParameters = TypeVar("PrimitiveParameters", bound=BaseModel)
PrimitivePayload = TypeVar("PrimitivePayload", bound=FactPayloadBase)
PrimitiveHandler = Callable[["PrimitiveContext", BaseModel], FactPayloadBase]


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    input_type: type[BaseModel]
    output_type: type[FactPayloadBase]
    handler: PrimitiveHandler


@dataclass(frozen=True, slots=True)
class PrimitiveContext:
    scope: EventScope
    manifests: tuple[SourceManifest, ...]
    events: tuple[CustomerEvent, ...]
    input_facts: tuple[AnalysisFact, ...]
    provenance: FactProvenance
    expected_metric_keys: tuple[str, ...]
    max_output_rows: int
    max_evidence: int
    registry: SourceRegistry
    budget: Budget

    @property
    def input_fact_ids(self) -> list[str]:
        return [fact.fact_id for fact in self.input_facts]


_FIELD_PATTERN = re.compile(r"^(?:(?P<source>[a-z][a-z0-9_]{1,63})\.)?(?P<field>[a-z][a-z0-9_]*)$")
_BINARY_PREDICATE_PATTERN = re.compile(
    r"^(?P<field>(?:[a-z][a-z0-9_]{1,63}\.)?[a-z][a-z0-9_]*)\s*"
    r"(?P<operator>==|!=|<=|>=|=|<|>|contains)\s*(?P<value>.+)$",
    flags=re.IGNORECASE,
)
_NULL_PREDICATE_PATTERN = re.compile(
    r"^(?P<field>(?:[a-z][a-z0-9_]{1,63}\.)?[a-z][a-z0-9_]*)\s+is\s+null$",
    flags=re.IGNORECASE,
)
_SET_PREDICATE_PATTERN = re.compile(
    r"^(?P<field>(?:[a-z][a-z0-9_]{1,63}\.)?[a-z][a-z0-9_]*)\s+"
    r"(?P<operator>in|not\s+in)\s*\[(?P<values>.*)\]$",
    flags=re.IGNORECASE,
)
_CANONICAL_FIELDS = frozenset(
    {
        "event_id",
        "evidence_id",
        "source_id",
        "occurred_at",
        "event_type",
        "action",
        "topic",
        "outcome",
        "canonical_customer_id",
    }
)
_MISSING = object()


def checkpoint_each(context: PrimitiveContext, values: Iterable[PrimitiveParameters]):
    for value in values:
        context.budget.checkpoint()
        yield value


def semantic_value(event: CustomerEvent, field_ref: str):
    match = _FIELD_PATTERN.fullmatch(field_ref.strip())
    if match is None:
        raise PrimitiveContractError(f"invalid semantic field reference: {field_ref}")
    source_id = match.group("source")
    field = match.group("field")
    if source_id is not None and event.source_id != source_id:
        return _MISSING
    if field in _CANONICAL_FIELDS:
        return getattr(event, field)
    if field in event.dimensions:
        return event.dimensions[field]
    if field in event.measures:
        return event.measures[field]
    return _MISSING


def matches_predicate(event: CustomerEvent, predicate: str) -> bool:
    expression = predicate.strip()
    null_match = _NULL_PREDICATE_PATTERN.fullmatch(expression)
    if null_match is not None:
        value = semantic_value(event, null_match.group("field"))
        return value is _MISSING or value is None

    set_match = _SET_PREDICATE_PATTERN.fullmatch(expression)
    if set_match is not None:
        value = semantic_value(event, set_match.group("field"))
        candidates = [_parse_literal(item) for item in _split_values(set_match.group("values"))]
        contains = value is not _MISSING and value in candidates
        return not contains if "not" in set_match.group("operator").lower() else contains

    match = _BINARY_PREDICATE_PATTERN.fullmatch(expression)
    if match is None:
        raise PrimitiveContractError(f"unsupported predicate: {predicate}")
    actual = semantic_value(event, match.group("field"))
    if actual is _MISSING:
        return False
    expected = _parse_literal(match.group("value"))
    operator = match.group("operator").lower()
    if operator in {"=", "=="}:
        return actual == expected
    if operator == "!=":
        return actual != expected
    if operator == "contains":
        return isinstance(actual, str) and str(expected) in actual
    try:
        if operator == "<":
            return actual < expected
        if operator == "<=":
            return actual <= expected
        if operator == ">":
            return actual > expected
        if operator == ">=":
            return actual >= expected
    except TypeError:
        return False
    raise PrimitiveContractError(f"unsupported predicate operator: {operator}")


def filter_events(
    context: PrimitiveContext,
    predicates: Sequence[str],
) -> list[CustomerEvent]:
    selected: list[CustomerEvent] = []
    for event in checkpoint_each(context, context.events):
        if all(matches_predicate(event, predicate) for predicate in predicates):
            selected.append(event)
    return selected


def dimension_values(event: CustomerEvent, fields: Sequence[str]) -> dict[str, DimensionValue]:
    dimensions: dict[str, DimensionValue] = {}
    for field in fields:
        value = semantic_value(event, field)
        if isinstance(value, datetime):
            dimensions[field] = value.isoformat()
        else:
            dimensions[field] = None if value is _MISSING else value
    return dimensions


def field_ref(field: str, manifests: Sequence[SourceManifest]) -> FieldRef:
    match = _FIELD_PATTERN.fullmatch(field.strip())
    if match is None:
        raise PrimitiveContractError(f"invalid semantic field reference: {field}")
    source_id = match.group("source")
    name = match.group("field")
    selected = [manifest for manifest in manifests if source_id in {None, manifest.source_id}]
    if name in _CANONICAL_FIELDS:
        kind = "canonical"
    elif any(name in manifest.dimensions for manifest in selected):
        kind = "dimension"
    elif any(name in manifest.measures for manifest in selected):
        kind = "measure"
    else:
        raise PrimitiveContractError(f"undeclared semantic field reference: {field}")
    return FieldRef(field=name, field_kind=kind, source_id=source_id)


def metric(
    metric_key: str,
    value: int | FiniteNumber,
    *,
    unit: str,
    dimensions: dict[str, DimensionValue] | None = None,
) -> AnalysisMetricFact:
    return AnalysisMetricFact(
        metric_key=metric_key,
        label=metric_key.replace("_", " ").title(),
        value=value,
        unit=unit,
        dimensions=dimensions or {},
    )


def evidence_allowlist(events: Sequence[CustomerEvent], limit: int) -> list[str]:
    identifiers: list[str] = []
    seen: set[str] = set()
    for event in events:
        if len(identifiers) >= limit:
            break
        if event.evidence_id not in seen:
            identifiers.append(event.evidence_id)
            seen.add(event.evidence_id)
    return identifiers


def stable_identifier(prefix: str, value: BaseModel | dict | list | str) -> str:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{sha256(serialized.encode('utf-8')).hexdigest()[:24]}"


def require_single_expected_metric(context: PrimitiveContext) -> str:
    if len(context.expected_metric_keys) != 1:
        raise PrimitiveContractError("primitive requires exactly one expected metric key")
    return context.expected_metric_keys[0]


def _parse_literal(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _split_values(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [value.strip() for value in raw.split(",")]


__all__ = [
    "HandlerSpec",
    "NoDataScope",
    "PrimitiveCancelledError",
    "PrimitiveContext",
    "PrimitiveContractError",
    "PrimitiveDependencyError",
    "PrimitiveExecutionError",
    "PrimitiveLimitError",
    "PrimitiveScopeError",
    "PrimitiveTimeoutError",
    "checkpoint_each",
    "dimension_values",
    "evidence_allowlist",
    "field_ref",
    "filter_events",
    "matches_predicate",
    "metric",
    "require_single_expected_metric",
    "semantic_value",
    "stable_identifier",
]
