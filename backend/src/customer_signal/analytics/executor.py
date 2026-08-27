"""Bounded, deterministic executor for server-owned generic primitives."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from threading import Event
from types import MappingProxyType

from pydantic import BaseModel, ValidationError

from customer_signal.analytics.primitives import HANDLERS, HandlerSpec
from customer_signal.analytics.primitives.common import (
    NoDataScope,
    PrimitiveCancelledError,
    PrimitiveContext,
    PrimitiveContractError,
    PrimitiveDependencyError,
    PrimitiveExecutionError,
    PrimitiveLimitError,
    PrimitiveScopeError,
    PrimitiveTimeoutError,
)
from customer_signal.data.source_registry import SourceRegistry, validate_adapter_contract
from customer_signal.domain.analysis import AnalysisStep
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisFact,
    CatalogSourcesPayload,
    CustomerJourneyPayload,
    CustomerRankingPayload,
    EvidencePayload,
    FactPayloadBase,
    FactProvenance,
    ProfileEventsPayload,
    RepetitionPayload,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
    SequenceMatchPayload,
    build_fact,
    extract_fact_projection,
)
from customer_signal.domain.models import CustomerEvent
from customer_signal.domain.primitive_catalog import dependency_arity_table
from customer_signal.domain.primitives import (
    GetCustomerJourneyInput,
    GetEvidenceInput,
    RankCustomersInput,
)
from customer_signal.domain.sources import EventScope
from customer_signal.domain.types import GenericPrimitiveName


_DEPENDENCY_ARITY: dict[GenericPrimitiveName, tuple[int, int]] = dependency_arity_table()


class RunBudget:
    """One monotonic deadline and cancellation flag shared by child step budgets."""

    def __init__(
        self,
        *,
        deadline_monotonic: float,
        monotonic: Callable[[], float] = time.monotonic,
        _cancelled: Event | None = None,
    ) -> None:
        if isinstance(deadline_monotonic, bool) or not isinstance(deadline_monotonic, (int, float)):
            raise TypeError("deadline_monotonic must be a finite number")
        if not math.isfinite(deadline_monotonic):
            raise ValueError("deadline_monotonic must be a finite number")
        self._deadline_monotonic = float(deadline_monotonic)
        self._monotonic = monotonic
        self._cancelled = _cancelled or Event()

    @property
    def deadline_monotonic(self) -> float:
        return self._deadline_monotonic

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def child(self, *, timeout_seconds: float) -> RunBudget:
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be finite and positive")
        return RunBudget(
            deadline_monotonic=min(
                self._deadline_monotonic,
                self._monotonic() + timeout_seconds,
            ),
            monotonic=self._monotonic,
            _cancelled=self._cancelled,
        )

    def cancel(self) -> None:
        self._cancelled.set()

    def checkpoint(self) -> None:
        if self._cancelled.is_set():
            raise PrimitiveCancelledError("primitive execution was cancelled")
        if self._monotonic() >= self._deadline_monotonic:
            raise PrimitiveTimeoutError("primitive execution exceeded its deadline")


class PrimitiveExecutor:
    """Execute one validated AnalysisStep without widening scope or publishing partial data."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        dataset_version: str,
        handlers: Mapping[GenericPrimitiveName, HandlerSpec] = HANDLERS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(dataset_version, str) or not dataset_version:
            raise ValueError("dataset_version must be nonblank")
        self._registry = registry
        self._dataset_version = dataset_version
        self._handlers = MappingProxyType(dict(handlers))
        self._clock = clock

    @property
    def handlers(self) -> Mapping[GenericPrimitiveName, HandlerSpec]:
        return self._handlers

    def execute(
        self,
        step: AnalysisStep,
        *,
        scope: EventScope,
        prior_facts: Sequence[AnalysisFact],
        budget: RunBudget,
    ) -> AnalysisFact:
        budget.checkpoint()
        inputs = _resolve_dependencies(step, prior_facts)
        _validate_parameter_limits(step)
        step_scope = _restrict_scope(step, scope)
        step_budget = budget.child(timeout_seconds=step.limits.timeout_seconds)
        step_budget.checkpoint()

        try:
            manifests = tuple(self._registry.manifests(step_scope.source_ids))
        except LookupError as error:
            raise PrimitiveScopeError("step references an unregistered source") from error
        for manifest in manifests:
            if step.primitive not in manifest.capabilities:
                raise PrimitiveScopeError(
                    f"source {manifest.source_id} does not support {step.primitive}"
                )
        provenance = FactProvenance(
            scope=step_scope,
            source_ids=list(step_scope.source_ids),
            adapter_versions={
                manifest.source_id: manifest.adapter_version for manifest in manifests
            },
            manifest_versions={
                manifest.source_id: manifest.manifest_version for manifest in manifests
            },
            dataset_version=self._dataset_version,
        )
        _validate_dependency_provenance(inputs, provenance)
        events = self._load_complete_events(step_scope, step_budget)
        if not events:
            raise NoDataScope(provenance)
        _validate_dependency_authorization(inputs, events)

        try:
            spec = self._handlers[step.primitive]
        except KeyError as error:
            raise PrimitiveContractError("primitive has no registered handler") from error
        parameters = _validate_parameters(spec, step)
        context = PrimitiveContext(
            scope=step_scope,
            manifests=manifests,
            events=tuple(events),
            input_facts=tuple(inputs),
            provenance=provenance,
            expected_metric_keys=tuple(step.expected_output.required_metric_keys),
            max_output_rows=step.limits.max_output_rows,
            max_evidence=step.limits.max_evidence,
            registry=self._registry,
            budget=step_budget,
        )
        step_budget.checkpoint()
        try:
            raw_payload = spec.handler(context, parameters)
        except PrimitiveExecutionError:
            raise
        except (TypeError, ValueError) as error:
            raise PrimitiveContractError("primitive handler rejected its typed input") from error
        step_budget.checkpoint()
        payload = _validate_handler_payload(spec, raw_payload)

        _validate_payload_limits(payload, step)
        _validate_payload_contract(payload, step, context)
        step_budget.checkpoint()
        result_id, fact_id = _stable_fact_identifiers(step, context)
        created_at = self._clock()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise PrimitiveContractError("executor clock must return an aware datetime")
        try:
            fact = build_fact(
                fact_id=fact_id,
                step_id=step.step_id,
                primitive=step.primitive,
                result_id=result_id,
                payload=payload,
                scope=step_scope,
                created_at=created_at,
                input_facts=inputs,
            )
            from customer_signal.agent.plan_validator import validate_fact_against_step

            validate_fact_against_step(step, fact)
        except PrimitiveExecutionError:
            raise
        except (TypeError, ValueError) as error:
            raise PrimitiveContractError("primitive payload cannot publish a valid Fact") from error
        step_budget.checkpoint()
        return fact

    async def execute_async(
        self,
        step: AnalysisStep,
        *,
        scope: EventScope,
        prior_facts: Sequence[AnalysisFact],
        budget: RunBudget,
    ) -> AnalysisFact:
        worker = asyncio.create_task(
            asyncio.to_thread(
                self.execute,
                step,
                scope=scope,
                prior_facts=prior_facts,
                budget=budget,
            )
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            budget.cancel()
            try:
                await worker
            except Exception:
                pass
            raise

    def _load_complete_events(
        self,
        scope: EventScope,
        budget: RunBudget,
    ) -> list[CustomerEvent]:
        budget.checkpoint()
        probe_limit = min(10_000, scope.max_events + 1)
        events: list[CustomerEvent] = []
        event_ids: set[str] = set()
        evidence_ids: set[str] = set()
        for source_id in scope.source_ids:
            budget.checkpoint()
            source_scope = scope.model_copy(
                update={"source_ids": [source_id], "max_events": probe_limit}
            )
            try:
                source_events = validate_adapter_contract(
                    self._registry.get(source_id),
                    source_scope,
                )
            except LookupError as error:
                raise PrimitiveScopeError("step references an unregistered source") from error
            except ValueError as error:
                if "more events than max_events" in str(error):
                    raise PrimitiveLimitError("input events exceed max_input_events") from error
                raise PrimitiveContractError(
                    "source registry rejected the bounded event read"
                ) from error
            if probe_limit == 10_000 and len(source_events) == probe_limit:
                raise PrimitiveLimitError("input completeness is ambiguous at the hard event limit")
            for event in source_events:
                if event.event_id in event_ids or event.evidence_id in evidence_ids:
                    raise PrimitiveContractError(
                        "source registry returned duplicate event or evidence identifiers"
                    )
                event_ids.add(event.event_id)
                evidence_ids.add(event.evidence_id)
                events.append(event)
            if len(events) > scope.max_events:
                raise PrimitiveLimitError("input events exceed max_input_events")
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        budget.checkpoint()
        return events


def _resolve_dependencies(
    step: AnalysisStep,
    prior_facts: Sequence[AnalysisFact],
) -> list[AnalysisFact]:
    minimum, maximum = _DEPENDENCY_ARITY[step.primitive]
    if not minimum <= len(step.input_step_ids) <= maximum:
        raise PrimitiveDependencyError("primitive dependency arity is invalid")
    by_step: dict[str, AnalysisFact] = {}
    fact_ids: set[str] = set()
    for fact in prior_facts:
        if fact.step_id in by_step or fact.fact_id in fact_ids:
            raise PrimitiveDependencyError("dependency Fact ledger contains duplicates")
        by_step[fact.step_id] = fact
        fact_ids.add(fact.fact_id)
    missing = [step_id for step_id in step.input_step_ids if step_id not in by_step]
    if missing:
        raise PrimitiveDependencyError("dependency Fact is missing")
    return [by_step[step_id] for step_id in step.input_step_ids]


def _restrict_scope(step: AnalysisStep, scope: EventScope) -> EventScope:
    if not set(step.source_ids) <= set(scope.source_ids):
        raise PrimitiveScopeError("step source selection expands the authorized source scope")
    return EventScope(
        start_at=scope.start_at,
        end_at=scope.end_at,
        source_ids=list(step.source_ids),
        max_events=min(scope.max_events, step.limits.max_input_events),
    )


def _validate_dependency_provenance(
    input_facts: Sequence[AnalysisFact],
    current: FactProvenance,
) -> None:
    current_sources = set(current.source_ids)
    for fact in input_facts:
        dependency = fact.payload.provenance
        if dependency.dataset_version != current.dataset_version:
            raise PrimitiveDependencyError("dependency dataset version is not current")
        if not set(fact.source_ids) <= current_sources:
            raise PrimitiveDependencyError("dependency source scope is not covered by the step")
        if (
            dependency.scope.start_at != current.scope.start_at
            or dependency.scope.end_at != current.scope.end_at
        ):
            raise PrimitiveDependencyError("dependency time scope does not match the step")
        for source_id in fact.source_ids:
            if (
                dependency.adapter_versions[source_id] != current.adapter_versions[source_id]
                or dependency.manifest_versions[source_id] != current.manifest_versions[source_id]
            ):
                raise PrimitiveDependencyError("dependency source version is not current")


def _validate_dependency_authorization(
    input_facts: Sequence[AnalysisFact],
    events: Sequence[CustomerEvent],
) -> None:
    customer_ids = {event.canonical_customer_id for event in events}
    evidence_ids = {event.evidence_id for event in events}
    for fact in input_facts:
        if not set(fact.customer_ids) <= customer_ids:
            raise PrimitiveDependencyError(
                "dependency customer is outside the restricted event scope"
            )
        if not set(fact.evidence_ids) <= evidence_ids:
            raise PrimitiveDependencyError(
                "dependency evidence is outside the restricted event scope"
            )


def _validate_parameter_limits(step: AnalysisStep) -> None:
    parameters = step.parameters
    if isinstance(parameters, (RankCustomersInput, GetCustomerJourneyInput)) and (
        parameters.limit > step.limits.max_output_rows
    ):
        raise PrimitiveLimitError("parameter limit exceeds max_output_rows")
    if isinstance(parameters, GetEvidenceInput) and (
        parameters.limit > step.limits.max_output_rows
        or parameters.limit > step.limits.max_evidence
    ):
        raise PrimitiveLimitError("evidence parameter limit exceeds step limits")


def _validate_parameters(spec: HandlerSpec, step: AnalysisStep) -> BaseModel:
    try:
        parameters = spec.input_type.model_validate(
            step.parameters.model_dump(mode="python"),
            strict=True,
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise PrimitiveContractError(
            "step parameters do not match the registered handler"
        ) from error
    if getattr(parameters, "primitive", None) != step.primitive:
        raise PrimitiveContractError("handler input discriminator does not match the step")
    return parameters


def _validate_handler_payload(
    spec: HandlerSpec,
    raw_payload: FactPayloadBase,
) -> FactPayloadBase:
    try:
        data = (
            raw_payload.model_dump(mode="python")
            if isinstance(raw_payload, BaseModel)
            else raw_payload
        )
        return spec.output_type.model_validate(data, strict=True)
    except (ValidationError, TypeError, ValueError) as error:
        raise PrimitiveContractError("handler returned the wrong payload contract") from error


def _validate_payload_limits(payload: FactPayloadBase, step: AnalysisStep) -> None:
    row_count = _payload_row_count(payload)
    if row_count > step.limits.max_output_rows:
        raise PrimitiveLimitError("handler payload exceeds max_output_rows")
    projection = extract_fact_projection(payload)
    if len(projection.evidence_ids) > step.limits.max_evidence:
        raise PrimitiveLimitError("handler payload exceeds max_evidence")


def _validate_payload_contract(
    payload: FactPayloadBase,
    step: AnalysisStep,
    context: PrimitiveContext,
) -> None:
    if payload.kind != step.primitive:
        raise PrimitiveContractError("payload kind does not match the step primitive")
    if payload.input_fact_ids != context.input_fact_ids:
        raise PrimitiveContractError("payload dependency Fact IDs are not authoritative")
    if payload.provenance != context.provenance:
        raise PrimitiveContractError("payload provenance is not authoritative")
    if payload.processing.scanned_events != len(context.events):
        raise PrimitiveContractError("payload scanned_events does not match the bounded input")
    if payload.processing.returned_rows != _payload_row_count(payload):
        raise PrimitiveContractError("payload returned_rows does not match its typed records")
    metric_keys = {metric_fact.metric_key for metric_fact in payload.metrics}
    if not set(step.expected_output.required_metric_keys) <= metric_keys:
        raise PrimitiveContractError("payload is missing a required metric")
    if isinstance(payload, (AggregateEventsPayload, SegmentComparisonPayload)) and (
        payload.requested_metric_key not in step.expected_output.required_metric_keys
    ):
        raise PrimitiveContractError("requested metric is not declared by expected_output")

    projection = extract_fact_projection(payload)
    authorized_evidence = {event.evidence_id for event in context.events}
    authorized_customers = {event.canonical_customer_id for event in context.events}
    if not set(projection.evidence_ids) <= authorized_evidence:
        raise PrimitiveContractError("payload evidence is outside the restricted event scope")
    if not set(projection.customer_ids) <= authorized_customers:
        raise PrimitiveContractError("payload customer is outside the restricted authorization")


def _payload_row_count(payload: FactPayloadBase) -> int:
    if isinstance(payload, CatalogSourcesPayload):
        return len(payload.sources)
    if isinstance(payload, ProfileEventsPayload):
        return len(payload.distributions)
    if isinstance(payload, AggregateEventsPayload):
        return len(payload.buckets) + len(payload.series)
    if isinstance(payload, SegmentCustomersPayload):
        return len(payload.customer_ids)
    if isinstance(payload, RepetitionPayload):
        return len(payload.matches)
    if isinstance(payload, SequenceMatchPayload):
        return len(payload.matches)
    if isinstance(payload, SegmentComparisonPayload):
        return len(payload.deltas)
    if isinstance(payload, CustomerRankingPayload):
        return len(payload.customers)
    if isinstance(payload, CustomerJourneyPayload):
        return len(payload.events)
    if isinstance(payload, EvidencePayload):
        return len(payload.records)
    raise PrimitiveContractError("unknown payload row projection")


def _stable_fact_identifiers(
    step: AnalysisStep,
    context: PrimitiveContext,
) -> tuple[str, str]:
    identity = {
        "primitive": step.primitive,
        "step_id": step.step_id,
        "parameters": step.parameters.model_dump(mode="json"),
        "input_fact_ids": context.input_fact_ids,
        "limits": step.limits.model_dump(mode="json"),
        "scope": context.scope.model_dump(mode="json"),
        "adapter_versions": context.provenance.adapter_versions,
        "manifest_versions": context.provenance.manifest_versions,
        "dataset_version": context.provenance.dataset_version,
    }
    result_id = _hash_identifier("result", identity)
    fact_id = _hash_identifier(
        "fact",
        {
            "result_id": result_id,
            "step_id": step.step_id,
            "primitive": step.primitive,
            "input_fact_ids": context.input_fact_ids,
        },
    )
    return result_id, fact_id


def _hash_identifier(prefix: str, value: dict) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{prefix}-{sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


__all__ = [
    "NoDataScope",
    "PrimitiveCancelledError",
    "PrimitiveContractError",
    "PrimitiveDependencyError",
    "PrimitiveExecutor",
    "PrimitiveExecutionError",
    "PrimitiveLimitError",
    "PrimitiveScopeError",
    "PrimitiveTimeoutError",
    "RunBudget",
]
