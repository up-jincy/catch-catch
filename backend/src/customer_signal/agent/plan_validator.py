"""Fail-closed validation for model-authored generic goals and plans."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from pydantic import ValidationError

from customer_signal.agent.contracts import RunRequest
from customer_signal.domain.analysis import AnalysisGoal, AnalysisPlan, AnalysisStep
from customer_signal.domain.facts import (
    AggregateEventsPayload,
    AnalysisFact,
    FieldRef,
    SegmentComparisonPayload,
)
from customer_signal.domain.primitives import (
    AggregateEventsInput,
    CompareSegmentsInput,
    DetectRepetitionInput,
    ProfileEventsInput,
    SegmentCustomersInput,
)
from customer_signal.domain.sources import SourceManifest


class PlanValidationError(ValueError):
    """Raised when a goal or Plan expands authorization or cannot be executed safely."""


_CANONICAL_FIELDS = frozenset(
    {"event_type", "action", "topic", "outcome", "source_id", "occurred_at"}
)
_SENSITIVE_TOKENS = frozenset(
    {
        "email",
        "phone",
        "name",
        "address",
        "identity",
        "raw",
        "password",
        "secret",
        "token",
    }
)
_FIELD_REF_TEXT = r"(?:(?P<source>[a-z][a-z0-9_]{1,63})\.)?(?P<field>[a-z][a-z0-9_]*)"
_FIELD_REF_PATTERN = re.compile(rf"^{_FIELD_REF_TEXT}$")
_PREDICATE_PATTERN = re.compile(
    rf"""
    ^\s*{_FIELD_REF_TEXT}
    (?:
        \s*(?:==|!=|<=|>=|<|>|=|contains)\s*
        (?:'[^'\r\n]{{0,256}}'|"[^"\r\n]{{0,256}}"|-?\d+(?:\.\d+)?|true|false|null|[^\s;()[\]{{}}]+)
      | \s+(?:in|not\s+in)\s+\[(?:[^;\[\]\r\n]{{0,512}})\]
      | \s+is\s+null
    )?
    \s*$
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


def validate_goal_against_request(
    goal: AnalysisGoal,
    request: RunRequest,
    manifests: Sequence[SourceManifest] | None = None,
) -> None:
    """Ensure a model-created Goal only narrows the caller's original scope."""

    if not request.start_at <= goal.time_range.start_at < goal.time_range.end_at <= request.end_at:
        raise PlanValidationError("goal time range must remain within the original request")
    if not goal.source_ids:
        raise PlanValidationError("goal source selection must be nonempty")
    if not set(goal.source_ids) <= set(request.enabled_sources):
        raise PlanValidationError("goal source selection cannot add an unenabled source")
    for field in _goal_field_refs(goal):
        if field.source_id is not None and field.source_id not in goal.source_ids:
            raise PlanValidationError("FieldRef source_id must be a selected source")
        if any(token in field.field.split("_") for token in _SENSITIVE_TOKENS):
            raise PlanValidationError("PII or raw FieldRef use is not allowed")
    if manifests is not None:
        manifest_by_source = {manifest.source_id: manifest for manifest in manifests}
        if len(manifest_by_source) != len(manifests):
            raise PlanValidationError("source manifests must be unique")
        unknown = set(goal.source_ids) - set(manifest_by_source)
        if unknown:
            raise PlanValidationError("goal references a source without an enabled manifest")
        selected = [manifest_by_source[source_id] for source_id in goal.source_ids]
        for field in goal.group_by:
            _validate_typed_field_ref(field, selected)
        for measure in goal.measures:
            if measure.field is not None:
                _validate_typed_field_ref(measure.field, selected)
        for predicate in goal.predicates:
            _validate_typed_field_ref(predicate.field, selected)


def validate_plan(plan: AnalysisPlan, manifests: Sequence[SourceManifest]) -> None:
    """Validate topology, capability, semantic fields, and execution bounds."""

    plan = _revalidate_plan(plan)
    manifest_by_source: dict[str, SourceManifest] = {}
    for manifest in manifests:
        if manifest.source_id in manifest_by_source:
            raise PlanValidationError("source manifests must be unique")
        manifest_by_source[manifest.source_id] = manifest
    if not manifest_by_source:
        raise PlanValidationError("at least one enabled source manifest is required")

    _validate_topology_and_arity(plan.steps)
    for step in plan.steps:
        selected = _selected_manifests(step, manifest_by_source)
        _validate_step_capability(step, selected)
        _validate_step_fields(step, selected)


def validate_plan_revision(
    *,
    previous: AnalysisPlan,
    revised: AnalysisPlan,
    completed_step_ids: set[str] | frozenset[str],
    manifests: Sequence[SourceManifest],
) -> None:
    """Validate a revision while treating every completed Step as immutable."""

    previous = _revalidate_plan(previous)
    revised = _revalidate_plan(revised)
    if revised.plan_id != previous.plan_id or revised.goal_id != previous.goal_id:
        raise PlanValidationError("plan revision must preserve plan_id and goal_id")
    if revised.revision <= previous.revision:
        raise PlanValidationError("plan revision must increase")

    previous_by_id = {step.step_id: (index, step) for index, step in enumerate(previous.steps)}
    revised_by_id = {step.step_id: (index, step) for index, step in enumerate(revised.steps)}
    unknown_completed = set(completed_step_ids) - set(previous_by_id)
    if unknown_completed:
        raise PlanValidationError("completed step is absent from the previous plan")
    for step_id in completed_step_ids:
        old_index, old_step = previous_by_id[step_id]
        replacement = revised_by_id.get(step_id)
        if replacement is None:
            raise PlanValidationError("completed step is immutable")
        new_index, new_step = replacement
        if old_index != new_index or old_step != new_step:
            raise PlanValidationError("completed step is immutable")

    validate_plan(revised, manifests)


def validate_fact_against_step(step: AnalysisStep, fact: AnalysisFact) -> None:
    """Bind a server Fact's published metrics and scope to its validated Step contract."""

    if fact.step_id != step.step_id or fact.primitive != step.primitive:
        raise PlanValidationError("Fact identity and primitive must match the validated step")
    if fact.source_ids != step.source_ids:
        raise PlanValidationError("Fact sources must exactly match the validated step scope")
    metric_keys = {metric.metric_key for metric in fact.metrics}
    missing = set(step.expected_output.required_metric_keys) - metric_keys
    if missing:
        raise PlanValidationError("Fact is missing a step-required canonical metric")
    if isinstance(fact.payload, (AggregateEventsPayload, SegmentComparisonPayload)) and (
        fact.payload.requested_metric_key not in step.expected_output.required_metric_keys
    ):
        raise PlanValidationError(
            "payload requested metric must be declared by step expected_output"
        )
    if isinstance(fact.payload, SegmentComparisonPayload):
        if not isinstance(step.parameters, CompareSegmentsInput):
            raise PlanValidationError("comparison Fact requires compare step parameters")
        delta = fact.payload.deltas[0]
        if (
            delta.metric_key != step.parameters.metric_key
            or fact.payload.requested_metric_key != f"{step.parameters.metric_key}_delta"
        ):
            raise PlanValidationError(
                "comparison Fact metric must exactly match the compare step parameter"
            )


def _revalidate_plan(plan: AnalysisPlan) -> AnalysisPlan:
    try:
        return AnalysisPlan.model_validate(plan.model_dump())
    except (ValidationError, TypeError, ValueError) as error:
        message = str(error)
        if "dependency" in message or "prior steps" in message:
            raise PlanValidationError(f"invalid plan dependency: {message}") from error
        raise PlanValidationError(f"invalid bounded analysis plan: {message}") from error


def _goal_field_refs(goal: AnalysisGoal) -> Iterable[FieldRef]:
    yield from goal.group_by
    yield from (measure.field for measure in goal.measures if measure.field is not None)
    yield from (predicate.field for predicate in goal.predicates)


def _validate_topology_and_arity(steps: Sequence[AnalysisStep]) -> None:
    """Repeat model invariants at the trust boundary for constructed/mutated values."""

    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise PlanValidationError("step IDs must be unique")
    if not 3 <= len(steps) <= 6:
        raise PlanValidationError("analysis plan must contain three to six steps")

    arity = {
        "catalog_sources": (0, 0),
        "profile_events": (0, 0),
        "aggregate_events": (0, 0),
        "segment_customers": (0, 0),
        "detect_repetition": (0, 0),
        "match_sequence": (0, 0),
        "compare_segments": (2, 2),
        "rank_customers": (1, 4),
        "get_customer_journey": (1, 1),
        "get_evidence": (1, 1),
    }
    prior: set[str] = set()
    for step in steps:
        if len(step.input_step_ids) != len(set(step.input_step_ids)):
            raise PlanValidationError("step dependency IDs must be unique")
        if any(step_id not in prior for step_id in step.input_step_ids):
            raise PlanValidationError("step dependency must reference a prior step")
        minimum, maximum = arity[step.primitive]
        if not minimum <= len(step.input_step_ids) <= maximum:
            raise PlanValidationError(f"{step.primitive} dependency arity is invalid")
        prior.add(step.step_id)


def _selected_manifests(
    step: AnalysisStep,
    manifest_by_source: dict[str, SourceManifest],
) -> list[SourceManifest]:
    unknown = [source_id for source_id in step.source_ids if source_id not in manifest_by_source]
    if unknown:
        raise PlanValidationError("step references an unknown or disabled source")
    if len(step.source_ids) != len(set(step.source_ids)):
        raise PlanValidationError("step source IDs must be unique")
    return [manifest_by_source[source_id] for source_id in step.source_ids]


def _validate_step_capability(
    step: AnalysisStep,
    manifests: Sequence[SourceManifest],
) -> None:
    unsupported = [
        manifest.source_id for manifest in manifests if step.primitive not in manifest.capabilities
    ]
    if unsupported:
        raise PlanValidationError(
            f"step capability {step.primitive} is unavailable for selected source"
        )


def _validate_step_fields(step: AnalysisStep, manifests: Sequence[SourceManifest]) -> None:
    for source_id, field_name in _parameter_field_refs(step):
        _validate_semantic_field(source_id, field_name, manifests)


def _parameter_field_refs(step: AnalysisStep) -> Iterable[tuple[str | None, str]]:
    parameters = step.parameters
    if isinstance(parameters, ProfileEventsInput):
        yield from (_parse_field_ref(field) for field in parameters.group_by)
        yield from (_predicate_field(predicate) for predicate in parameters.predicates)
    elif isinstance(parameters, AggregateEventsInput):
        yield from (_parse_field_ref(field) for field in parameters.group_by)
        yield from (_predicate_field(predicate) for predicate in parameters.predicates)
        if parameters.measure is not None:
            yield _parse_field_ref(parameters.measure)
    elif isinstance(parameters, SegmentCustomersInput):
        yield from (_predicate_field(predicate) for predicate in parameters.predicates)
    elif isinstance(parameters, DetectRepetitionInput):
        yield _parse_field_ref(parameters.topic_field)


def _parse_field_ref(value: str) -> tuple[str | None, str]:
    match = _FIELD_REF_PATTERN.fullmatch(value.strip())
    if match is None:
        raise PlanValidationError("field reference must be one explicit semantic field")
    return match.group("source"), match.group("field")


def _predicate_field(expression: str) -> tuple[str | None, str]:
    match = _PREDICATE_PATTERN.fullmatch(expression)
    if match is None:
        raise PlanValidationError("predicate must contain exactly one bounded semantic expression")
    return match.group("source"), match.group("field")


def _validate_semantic_field(
    source_id: str | None,
    field_name: str,
    manifests: Sequence[SourceManifest],
) -> None:
    normalized = field_name.strip()
    if any(token in normalized.split("_") for token in _SENSITIVE_TOKENS):
        raise PlanValidationError("PII or raw field use is not allowed")

    if source_id is not None:
        selected = next(
            (manifest for manifest in manifests if manifest.source_id == source_id), None
        )
        if selected is None:
            raise PlanValidationError("explicit field source must be a selected source")
        if normalized in _CANONICAL_FIELDS:
            return
        descriptor = selected.dimensions.get(normalized) or selected.measures.get(normalized)
        if descriptor is None:
            raise PlanValidationError(f"unknown field {source_id}.{normalized}")
        if descriptor.pii_classification != "none":
            raise PlanValidationError(f"PII field {source_id}.{normalized} is not allowed")
        return

    if normalized in _CANONICAL_FIELDS:
        return
    descriptors = [
        manifest.dimensions.get(normalized) or manifest.measures.get(normalized)
        for manifest in manifests
    ]
    if all(descriptor is None for descriptor in descriptors):
        raise PlanValidationError(f"unknown field {normalized}")
    if any(descriptor is None for descriptor in descriptors):
        raise PlanValidationError(
            f"unqualified field {normalized} must be declared by every selected source"
        )
    if any(descriptor.pii_classification != "none" for descriptor in descriptors if descriptor):
        raise PlanValidationError(f"PII field {normalized} is not allowed")


def _validate_typed_field_ref(
    field: FieldRef,
    manifests: Sequence[SourceManifest],
) -> None:
    if field.source_id is not None:
        selected = next(
            (manifest for manifest in manifests if manifest.source_id == field.source_id), None
        )
        if selected is None:
            raise PlanValidationError("FieldRef source_id must be a selected source")
        _validate_field_ref_on_manifest(field, selected)
        return
    for manifest in manifests:
        try:
            _validate_field_ref_on_manifest(field, manifest)
        except PlanValidationError as error:
            raise PlanValidationError(
                f"unqualified FieldRef {field.field} must be safe on every selected source"
            ) from error


def _validate_field_ref_on_manifest(field: FieldRef, manifest: SourceManifest) -> None:
    if field.field_kind == "canonical":
        if field.field not in _CANONICAL_FIELDS:
            raise PlanValidationError(f"unknown canonical FieldRef {field.field}")
        return
    descriptors = manifest.dimensions if field.field_kind == "dimension" else manifest.measures
    descriptor = descriptors.get(field.field)
    if descriptor is None:
        raise PlanValidationError(
            f"FieldRef {field.field} is not declared by source {manifest.source_id}"
        )
    if descriptor.pii_classification != "none":
        raise PlanValidationError(f"PII FieldRef {field.field} is not allowed")


__all__ = [
    "PlanValidationError",
    "validate_goal_against_request",
    "validate_fact_against_step",
    "validate_plan",
    "validate_plan_revision",
]
