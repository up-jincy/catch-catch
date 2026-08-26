"""Generic adapter projecting one mapped raw table onto the canonical event contract."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from customer_signal.domain.models import (
    CustomerEvent,
    EvidenceRecord,
    IdentityEdge,
    IdentityLinkType,
    IdentityRef,
)
from customer_signal.domain.sources import (
    DimensionDescriptor,
    EventScope,
    IdentityQualityDescriptor,
    MaskingPolicy,
    MeasureDescriptor,
    SourceManifest,
    TimeRange,
)
from customer_signal.domain.types import GenericPrimitiveName
from customer_signal.onboarding.profiler import load_rows
from customer_signal.onboarding.spec import FieldRule, SourceMappingSpec

ONBOARDED_ADAPTER_VERSION = "1"
_ALL_CAPABILITIES: frozenset[GenericPrimitiveName] = frozenset(
    {
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
    }
)
_TRUTHY = {"true", "t", "yes", "y", "1"}
_FALSY = {"false", "f", "no", "n", "0"}


class MappingError(ValueError):
    """A raw row could not be projected onto the canonical contract."""


def _apply_rule(rule: FieldRule, row: dict[str, object], field: str) -> str:
    if rule.const is not None:
        return rule.const
    column = cast(str, rule.column)
    raw = row.get(column)
    if raw is None:
        if rule.default is not None:
            return rule.default
        raise MappingError(f"{field}: column {column!r} is null and no default is set")
    value = str(raw)
    if rule.value_map is not None:
        mapped = rule.value_map.get(value, rule.default)
        if mapped is None:
            raise MappingError(f"{field}: value {value!r} is not covered by value_map")
        return mapped
    return value


def _as_bool(raw: object, column: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str) and raw.lower() in _TRUTHY | _FALSY:
        return raw.lower() in _TRUTHY
    raise MappingError(f"boolean dimension column {column!r} has non-boolean value {raw!r}")


def _as_timestamp(raw: object, column: str, timezone: str) -> datetime:
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as error:
            raise MappingError(
                f"timestamp column {column!r} has unparsable value {raw!r}"
            ) from error
    else:
        raise MappingError(f"timestamp column {column!r} has non-timestamp value {raw!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


class MappedTableAdapter:
    """Expose one spec-mapped raw table through the portable SourceAdapter contract."""

    def __init__(self, spec: SourceMappingSpec, columns: list[str], rows: list[tuple]) -> None:
        missing = spec.referenced_columns() - set(columns)
        if missing:
            raise MappingError(f"spec references missing columns: {sorted(missing)}")
        self._spec = spec
        self._events = self._build_events(spec, columns, rows)
        self._manifest = self._build_manifest(spec, self._events)
        self._edges = self._build_edges(spec, self._events)
        self.evidence_by_id = {event.evidence_id: _evidence_record(event) for event in self._events}

    @classmethod
    def from_file(cls, spec: SourceMappingSpec, path: Path) -> MappedTableAdapter:
        columns, rows = load_rows(path)
        return cls(spec, columns, rows)

    def describe(self) -> SourceManifest:
        return self._manifest

    def load_events(self, scope: EventScope) -> list[CustomerEvent]:
        self._validate_scope(scope)
        selected = [
            event for event in self._events if scope.start_at <= event.occurred_at < scope.end_at
        ]
        return selected[: scope.max_events]

    def load_identities(self, scope: EventScope) -> list[IdentityEdge]:
        self._validate_scope(scope)
        customers = {
            event.canonical_customer_id
            for event in self._events
            if scope.start_at <= event.occurred_at < scope.end_at
        }
        return [edge for edge in self._edges if edge.right.value in customers]

    def _validate_scope(self, scope: EventScope) -> None:
        if scope.source_ids != [self._spec.source_id]:
            raise ValueError("mapped adapter scope must select its source only")

    @staticmethod
    def _build_events(
        spec: SourceMappingSpec, columns: list[str], raw_rows: list[tuple]
    ) -> list[CustomerEvent]:
        drafts = []
        for row_index, raw in enumerate(raw_rows):
            row = dict(zip(columns, raw, strict=True))
            try:
                drafts.append(_project_row(spec, row))
            except MappingError as error:
                raise MappingError(f"row {row_index}: {error}") from error
        drafts.sort(key=lambda draft: draft[0])
        events = []
        for index, (occurred_at, fields) in enumerate(drafts):
            event_id = f"{spec.source_id}-{index:06d}"
            events.append(
                CustomerEvent(
                    event_id=event_id,
                    evidence_id=f"ev-{event_id}",
                    source_id=spec.source_id,
                    occurred_at=occurred_at,
                    **fields,
                )
            )
        return events

    @staticmethod
    def _build_manifest(spec: SourceMappingSpec, events: list[CustomerEvent]) -> SourceManifest:
        dimensions = {
            name: DimensionDescriptor(
                semantic_type=item.semantic_type,
                description=item.description,
                pii_classification=item.pii_classification,
            )
            for name, item in spec.dimensions.items()
        }
        measures = {
            name: MeasureDescriptor(
                semantic_type=item.semantic_type,
                description=item.description,
                unit=item.unit,
            )
            for name, item in spec.measures.items()
        }
        masking_rules = {
            name: item.masking for name, item in spec.dimensions.items() if item.masking is not None
        }
        return SourceManifest(
            source_id=spec.source_id,
            label=spec.label,
            description=spec.description,
            adapter_version=ONBOARDED_ADAPTER_VERSION,
            manifest_version="1",
            data_interval=TimeRange(
                start_at=min(event.occurred_at for event in events),
                end_at=max(event.occurred_at for event in events) + timedelta(microseconds=1),
            ),
            refresh_cadence="static_demo",
            supported_event_types=frozenset(event.event_type for event in events),
            supported_topics=frozenset(event.topic for event in events),
            supported_outcomes=frozenset(event.outcome for event in events),
            dimensions=dimensions,
            measures=measures,
            capabilities=_ALL_CAPABILITIES,
            masking_policy=MaskingPolicy(rules=masking_rules),
            identity_quality=IdentityQualityDescriptor(
                namespace=spec.identity.namespace,
                link_method=spec.identity.link_method,
                confidence=spec.identity.confidence,
            ),
        )

    @staticmethod
    def _build_edges(spec: SourceMappingSpec, events: list[CustomerEvent]) -> list[IdentityEdge]:
        link_type = cast(IdentityLinkType, spec.identity.link_method.upper())
        return [
            IdentityEdge(
                left=IdentityRef(namespace=spec.identity.namespace, value=customer),
                right=IdentityRef(namespace="canonical_customer", value=customer),
                link_type=link_type,
                confidence=spec.identity.confidence,
                provenance=f"onboarded:{spec.source_id}",
            )
            for customer in sorted({event.canonical_customer_id for event in events})
        ]


def _project_row(
    spec: SourceMappingSpec, row: dict[str, object]
) -> tuple[datetime, dict[str, object]]:
    occurred_at = _as_timestamp(
        row.get(spec.timestamp_column), spec.timestamp_column, spec.timezone
    )
    customer_raw = row.get(spec.identity.customer_column)
    if customer_raw is None:
        raise MappingError(f"customer column {spec.identity.customer_column!r} is null")
    customer = str(customer_raw)

    dimensions: dict[str, object] = {}
    for name, item in spec.dimensions.items():
        raw = row.get(item.column)
        if raw is None:
            dimensions[name] = None
        elif item.semantic_type == "boolean":
            dimensions[name] = _as_bool(raw, item.column)
        else:
            dimensions[name] = str(raw)

    measures: dict[str, object] = {}
    for name, item in spec.measures.items():
        raw = row.get(item.column)
        if raw is None:
            continue
        if item.semantic_type == "integer":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise MappingError(f"integer measure column {item.column!r} has value {raw!r}")
            measures[name] = raw
        else:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise MappingError(f"number measure column {item.column!r} has value {raw!r}")
            measures[name] = float(raw)

    return occurred_at, {
        "event_type": _apply_rule(spec.event_type, row, "event_type"),
        "action": _apply_rule(spec.action, row, "action"),
        "topic": _apply_rule(spec.topic, row, "topic"),
        "outcome": _apply_rule(spec.outcome, row, "outcome"),
        "text": _apply_rule(spec.text, row, "text"),
        "identities": [IdentityRef(namespace=spec.identity.namespace, value=customer)],
        "canonical_customer_id": customer,
        "dimensions": dimensions,
        "measures": measures,
    }


def _evidence_record(event: CustomerEvent) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=event.evidence_id,
        source_id=event.source_id,
        occurred_at=event.occurred_at,
        masked_customer_id="masked",
        summary=f"{event.event_type} event: topic={event.topic}; outcome={event.outcome}",
        raw_fields={},
    )


class CompositeEvidenceProvider:
    """Serve mapped-table evidence locally and delegate the rest to the base provider."""

    def __init__(self, base, adapters: Sequence[MappedTableAdapter]) -> None:
        self._base = base
        self._mapped: dict[str, EvidenceRecord] = {}
        for adapter in adapters:
            self._mapped.update(adapter.evidence_by_id)

    def get_evidence(self, allowed_evidence_ids: Sequence[str]) -> list[EvidenceRecord]:
        remaining = [
            evidence_id for evidence_id in allowed_evidence_ids if evidence_id not in self._mapped
        ]
        base_records = (
            {record.evidence_id: record for record in self._base.get_evidence(remaining)}
            if remaining
            else {}
        )
        return [
            self._mapped.get(evidence_id) or base_records[evidence_id]
            for evidence_id in allowed_evidence_ids
        ]


def load_onboarded_adapters(directory: Path) -> list[MappedTableAdapter]:
    """Build adapters for every approved spec registered under ``directory``."""

    if not directory.exists():
        return []
    adapters = []
    for spec_path in sorted(directory.glob("*/spec.json")):
        spec = SourceMappingSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
        if spec.status != "approved":
            continue
        data_files = [
            candidate
            for candidate in spec_path.parent.iterdir()
            if candidate.suffix.lower() in (".csv", ".parquet")
        ]
        if len(data_files) != 1:
            raise ValueError(f"{spec_path.parent} must contain exactly one data file")
        adapters.append(MappedTableAdapter.from_file(spec, data_files[0]))
    return adapters


__all__ = [
    "CompositeEvidenceProvider",
    "MappedTableAdapter",
    "MappingError",
    "ONBOARDED_ADAPTER_VERSION",
    "load_onboarded_adapters",
]
