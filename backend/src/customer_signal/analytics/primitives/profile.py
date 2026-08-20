"""Catalog, profile, and aggregate generic event primitives."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import cast

from customer_signal.analytics.primitives.common import (
    PrimitiveContext,
    PrimitiveContractError,
    PrimitiveLimitError,
    dimension_values,
    evidence_allowlist,
    field_ref,
    filter_events,
    metric,
    require_single_expected_metric,
    semantic_value,
)
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisAggregateBucket,
    AnalysisDistributionBucket,
    AnalysisQualityMetric,
    AnalysisSourceCatalogFact,
    AnalysisTimeBucket,
    CatalogSourcesPayload,
    ProcessingStats,
    ProfileEventsPayload,
)
from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CatalogSourcesInput,
    ProfileEventsInput,
)
from customer_signal.domain.sources import TimeRange
from customer_signal.domain.types import DimensionValue, FiniteNumber


def catalog_sources(
    context: PrimitiveContext,
    parameters: CatalogSourcesInput,
) -> CatalogSourcesPayload:
    del parameters
    context.budget.checkpoint()
    sources = [
        AnalysisSourceCatalogFact(
            source_id=manifest.source_id,
            data_interval=manifest.data_interval,
            row_count=sum(1 for event in context.events if event.source_id == manifest.source_id),
            manifest_version=manifest.manifest_version,
        )
        for manifest in sorted(context.manifests, key=lambda item: item.source_id)
    ]
    if len(sources) > context.max_output_rows:
        raise PrimitiveLimitError("catalog result exceeds max_output_rows")
    return CatalogSourcesPayload(
        kind="catalog_sources",
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(context.events),
            returned_rows=len(sources),
        ),
        provenance=context.provenance,
        metrics=[metric("source_count", len(sources), unit="sources")],
        sources=sources,
    )


def profile_events(
    context: PrimitiveContext,
    parameters: ProfileEventsInput,
) -> ProfileEventsPayload:
    _require_unique_fields(parameters.group_by)
    selected = filter_events(context, parameters.predicates)
    allowed_evidence = set(evidence_allowlist(selected, context.max_evidence))
    groups = _group_events(selected, parameters.group_by)
    distributions: list[AnalysisDistributionBucket] = []
    for dimensions, events in groups[: context.max_output_rows]:
        context.budget.checkpoint()
        distributions.append(
            AnalysisDistributionBucket(
                dimensions=dimensions,
                event_count=len(events),
                customer_count=len({event.canonical_customer_id for event in events}),
                evidence_ids=[
                    event.evidence_id for event in events if event.evidence_id in allowed_evidence
                ],
            )
        )

    quality: list[AnalysisQualityMetric] = []
    for name in parameters.group_by:
        context.budget.checkpoint()
        missing_count = sum(
            1 for event in selected if dimension_values(event, [name])[name] is None
        )
        total_count = len(selected)
        quality.append(
            AnalysisQualityMetric(
                field=field_ref(name, context.manifests),
                missing_count=missing_count,
                total_count=total_count,
                missing_rate=(float(missing_count / total_count) if total_count else 0.0),
            )
        )
    quality.sort(
        key=lambda item: (
            item.field.source_id or "",
            item.field.field_kind,
            item.field.field,
        )
    )
    metrics = [
        metric(
            "customer_count",
            len({event.canonical_customer_id for event in selected}),
            unit="customers",
        ),
        metric("event_count", len(selected), unit="events"),
    ]
    return ProfileEventsPayload(
        kind="profile_events",
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(selected),
            returned_rows=len(distributions),
        ),
        provenance=context.provenance,
        metrics=metrics,
        distributions=distributions,
        data_quality=quality,
    )


def aggregate_events(
    context: PrimitiveContext,
    parameters: AggregateEventsInput,
) -> AggregateEventsPayload:
    _require_unique_fields(parameters.group_by)
    requested_metric_key = require_single_expected_metric(context)
    selected = filter_events(context, parameters.predicates)
    unit = _metric_unit(context, parameters, requested_metric_key)
    allowed_evidence = set(evidence_allowlist(selected, context.max_evidence))

    buckets: list[AnalysisAggregateBucket] = []
    grouped = _group_events(selected, parameters.group_by)
    for dimensions, events in grouped[: context.max_output_rows]:
        context.budget.checkpoint()
        buckets.append(
            AnalysisAggregateBucket(
                dimensions=dimensions,
                metrics=[
                    metric(
                        requested_metric_key,
                        _aggregate_value(events, parameters, requested_metric_key),
                        unit=unit,
                        dimensions=dimensions,
                    )
                ],
                event_count=len(events),
                customer_count=len({event.canonical_customer_id for event in events}),
                evidence_ids=[
                    event.evidence_id for event in events if event.evidence_id in allowed_evidence
                ],
            )
        )

    remaining_rows = context.max_output_rows - len(buckets)
    series: list[AnalysisTimeBucket] = []
    for (start_at, dimensions), events in _time_groups(
        selected,
        parameters.group_by,
        parameters.time_grain,
    )[:remaining_rows]:
        context.budget.checkpoint()
        series.append(
            AnalysisTimeBucket(
                time_range=TimeRange(
                    start_at=start_at,
                    end_at=_next_bucket(start_at, parameters.time_grain),
                ),
                dimensions=dimensions,
                metrics=[
                    metric(
                        requested_metric_key,
                        _aggregate_value(events, parameters, requested_metric_key),
                        unit=unit,
                        dimensions=dimensions,
                    )
                ],
                evidence_ids=[
                    event.evidence_id for event in events if event.evidence_id in allowed_evidence
                ],
            )
        )

    returned_rows = len(buckets) + len(series)
    top_value = _aggregate_value(selected, parameters, requested_metric_key)
    return AggregateEventsPayload(
        kind="aggregate_events",
        requested_metric_key=requested_metric_key,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(selected),
            returned_rows=returned_rows,
        ),
        provenance=context.provenance,
        metrics=[metric(requested_metric_key, top_value, unit=unit)],
        buckets=buckets,
        series=series,
    )


def _require_unique_fields(fields: list[str]) -> None:
    if len(fields) != len(set(fields)):
        raise PrimitiveContractError("semantic group_by fields must be unique")


def _group_events(
    events: list[CustomerEvent],
    fields: list[str],
) -> list[tuple[dict[str, DimensionValue], list[CustomerEvent]]]:
    if not events:
        return []
    groups: dict[str, tuple[dict[str, DimensionValue], list[CustomerEvent]]] = {}
    for event in events:
        dimensions = dimension_values(event, fields)
        key = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        groups.setdefault(key, (dimensions, []))[1].append(event)
    return [groups[key] for key in sorted(groups)]


def _time_groups(
    events: list[CustomerEvent],
    fields: list[str],
    grain: str,
) -> list[tuple[tuple[datetime, dict[str, DimensionValue]], list[CustomerEvent]]]:
    groups: dict[
        tuple[datetime, str],
        tuple[dict[str, DimensionValue], list[CustomerEvent]],
    ] = {}
    for event in events:
        dimensions = dimension_values(event, fields)
        dimension_key = json.dumps(
            dimensions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        start_at = _bucket_start(event.occurred_at, grain)
        groups.setdefault((start_at, dimension_key), (dimensions, []))[1].append(event)
    return [
        ((start_at, dimensions), grouped_events)
        for (start_at, dimension_key), (dimensions, grouped_events) in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1])
        )
    ]


def _aggregate_value(
    events: list[CustomerEvent],
    parameters: AggregateEventsInput,
    metric_key: str,
) -> int | FiniteNumber:
    if parameters.aggregation == "count":
        if metric_key.endswith("customer_count"):
            return len({event.canonical_customer_id for event in events})
        return len(events)

    assert parameters.measure is not None
    values = [
        value
        for event in events
        if (value := semantic_value(event, parameters.measure)) is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ]
    if not values:
        return 0
    if parameters.aggregation == "sum":
        return cast(int | FiniteNumber, sum(values))
    if parameters.aggregation == "avg":
        return cast(FiniteNumber, float(sum(values) / len(values)))
    if parameters.aggregation == "min":
        return cast(int | FiniteNumber, min(values))
    if parameters.aggregation == "max":
        return cast(int | FiniteNumber, max(values))
    raise PrimitiveContractError("unsupported aggregation")


def _metric_unit(
    context: PrimitiveContext,
    parameters: AggregateEventsInput,
    metric_key: str,
) -> str:
    if metric_key.endswith("customer_count"):
        return "customers"
    if parameters.aggregation == "count":
        return "events"
    assert parameters.measure is not None
    name = parameters.measure.rsplit(".", 1)[-1]
    units = {
        descriptor.unit
        for manifest in context.manifests
        if (descriptor := manifest.measures.get(name)) is not None
    }
    if len(units) != 1:
        raise PrimitiveContractError("numeric measure must have one declared unit")
    return units.pop()


def _bucket_start(value: datetime, grain: str) -> datetime:
    if grain == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    day = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain == "day":
        return day
    if grain == "week":
        return day - timedelta(days=day.weekday())
    if grain == "month":
        return day.replace(day=1)
    raise PrimitiveContractError("unsupported time grain")


def _next_bucket(value: datetime, grain: str) -> datetime:
    if grain == "hour":
        return value + timedelta(hours=1)
    if grain == "day":
        return value + timedelta(days=1)
    if grain == "week":
        return value + timedelta(days=7)
    if grain == "month":
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1)
        return value.replace(month=value.month + 1)
    raise PrimitiveContractError("unsupported time grain")


__all__ = ["aggregate_events", "catalog_sources", "profile_events"]
