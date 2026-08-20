"""Small builders shared by the generic primitive RED tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from customer_signal.data.database import SYNTHETIC_DATASET_VERSION
from customer_signal.data.source_registry import SourceRegistry
from customer_signal.domain.analysis import (
    AnalysisStep,
    ContinueAfterStep,
    ExpectedOutputSpec,
    StepLimits,
)
from customer_signal.domain.facts import FactProvenance
from customer_signal.domain.models import EvidenceRecord, SyntheticDataset
from customer_signal.domain.primitives import AggregateEventsInput, MatchSequenceInput
from customer_signal.domain.sources import EventScope
from customer_signal.domain.types import GenericPrimitiveName, SourceId
from customer_signal.synthetic.manifest import synthetic_source_manifest
from support.in_memory_adapter import InMemorySourceAdapter


FIXED_CREATED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)
DATASET_VERSION = str(SYNTHETIC_DATASET_VERSION)


class _DatasetEvidenceProvider:
    def __init__(self, records: Sequence[EvidenceRecord]) -> None:
        self._records = {record.evidence_id: record for record in records}

    def get_evidence(self, allowed_evidence_ids: Sequence[str]) -> list[EvidenceRecord]:
        return [
            self._records[evidence_id].model_copy(update={"raw_fields": {}})
            for evidence_id in allowed_evidence_ids
        ]


def source_registry(
    dataset: SyntheticDataset,
    source_ids: Sequence[SourceId] | None = None,
) -> SourceRegistry:
    selected = list(
        source_ids
        or ("search_history", "search_feedback", "digital_behavior", "subscription", "voc")
    )
    adapters = [
        InMemorySourceAdapter(
            synthetic_source_manifest(source_id, dataset.events),
            [event for event in dataset.events if event.source_id == source_id],
            dataset.identity_edges,
            [record for record in dataset.evidence if record.source_id == source_id],
        )
        for source_id in selected
    ]
    return SourceRegistry(adapters, evidence=_DatasetEvidenceProvider(dataset.evidence))


def event_scope(
    dataset: SyntheticDataset,
    source_ids: Sequence[SourceId],
    *,
    max_events: int = 1_000,
) -> EventScope:
    selected = [event for event in dataset.events if event.source_id in source_ids]
    if not selected:
        raise ValueError("test scope requires at least one selected event")
    return EventScope(
        start_at=min(event.occurred_at for event in selected),
        end_at=max(event.occurred_at for event in selected) + timedelta(microseconds=1),
        source_ids=list(source_ids),
        max_events=max_events,
    )


def step_limits(
    *,
    max_input_events: int = 1_000,
    max_output_rows: int = 100,
    max_evidence: int = 20,
    timeout_seconds: float = 5.0,
) -> StepLimits:
    return StepLimits(
        max_input_events=max_input_events,
        max_output_rows=max_output_rows,
        max_evidence=max_evidence,
        timeout_seconds=timeout_seconds,
    )


def analysis_step(
    *,
    step_id: str,
    primitive: GenericPrimitiveName,
    parameters: object,
    source_ids: Sequence[SourceId],
    metric_keys: Sequence[str],
    input_step_ids: Sequence[str] = (),
    limits: StepLimits | None = None,
) -> AnalysisStep:
    return AnalysisStep(
        step_id=step_id,
        primitive=primitive,
        parameters=parameters,
        source_ids=list(source_ids),
        input_step_ids=list(input_step_ids),
        expected_output=ExpectedOutputSpec(
            payload_kind=primitive,
            required_metric_keys=sorted(metric_keys),
        ),
        stop_condition=ContinueAfterStep(),
        limits=limits or step_limits(),
    )


def negative_feedback_step(*, limits: StepLimits | None = None) -> AnalysisStep:
    return analysis_step(
        step_id="step-negative-feedback",
        primitive="aggregate_events",
        parameters=AggregateEventsInput(
            primitive="aggregate_events",
            aggregation="count",
            group_by=["topic"],
            predicates=["outcome == 'negative'", "topic == '요금제 변경'"],
            time_grain="day",
        ),
        source_ids=["search_feedback"],
        metric_keys=["negative_feedback_customer_count"],
        limits=limits,
    )


def repeat_to_voc_step() -> AnalysisStep:
    return analysis_step(
        step_id="step-repeat-to-voc",
        primitive="match_sequence",
        parameters=MatchSequenceInput(
            primitive="match_sequence",
            sequence=["search", "repeat_search", "contact_customer_service"],
        ),
        source_ids=["search_history", "voc"],
        metric_keys=["matched_customer_count"],
    )


def signup_abandonment_step() -> AnalysisStep:
    return analysis_step(
        step_id="step-signup-abandonment",
        primitive="match_sequence",
        parameters=MatchSequenceInput(
            primitive="match_sequence",
            sequence=["started", "completed"],
        ),
        source_ids=["subscription"],
        metric_keys=[
            "abandoned_customer_count",
            "matched_customer_count",
            "started_customer_count",
        ],
    )


def provenance(
    registry: SourceRegistry,
    scope: EventScope,
    *,
    dataset_version: str = DATASET_VERSION,
) -> FactProvenance:
    manifests = registry.manifests(scope.source_ids)
    return FactProvenance(
        scope=scope,
        source_ids=list(scope.source_ids),
        adapter_versions={manifest.source_id: manifest.adapter_version for manifest in manifests},
        manifest_versions={manifest.source_id: manifest.manifest_version for manifest in manifests},
        dataset_version=dataset_version,
    )
