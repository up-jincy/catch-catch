"""Fail-closed validation for model-authored generic goals and plans."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from pydantic import ValidationError

from customer_signal.agent.contracts import RunRequest
from customer_signal.domain.analysis import AnalysisGoal, AnalysisPlan, AnalysisStep
from customer_signal.domain.primitives import (
    AggregateEventsInput,
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
_FIELD_PREFIX = re.compile(r"^([a-z][a-z0-9_]*)")


def validate_goal_against_request(goal: AnalysisGoal, request: RunRequest) -> None:
    """Ensure a model-created Goal only narrows the caller's original scope."""

    if not request.start_at <= goal.time_range.start_at < goal.time_range.end_at <= request.end_at:
        raise PlanValidationError("goal time range must remain within the original request")
    if not goal.source_ids:
        raise PlanValidationError("goal source selection must be nonempty")
    if not set(goal.source_ids) <= set(request.enabled_sources):
        raise PlanValidationError("goal source selection cannot add an unenabled source")


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


def _revalidate_plan(plan: AnalysisPlan) -> AnalysisPlan:
    try:
        return AnalysisPlan.model_validate(plan.model_dump())
    except (ValidationError, TypeError, ValueError) as error:
        message = str(error)
        if "dependency" in message or "prior steps" in message:
            raise PlanValidationError(f"invalid plan dependency: {message}") from error
        raise PlanValidationError(f"invalid bounded analysis plan: {message}") from error


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
    for field_name in _parameter_field_names(step):
        _validate_semantic_field(field_name, manifests)


def _parameter_field_names(step: AnalysisStep) -> Iterable[str]:
    parameters = step.parameters
    if isinstance(parameters, ProfileEventsInput):
        yield from parameters.group_by
        yield from (_predicate_field(predicate) for predicate in parameters.predicates)
    elif isinstance(parameters, AggregateEventsInput):
        yield from parameters.group_by
        yield from (_predicate_field(predicate) for predicate in parameters.predicates)
        if parameters.measure is not None:
            yield parameters.measure
    elif isinstance(parameters, SegmentCustomersInput):
        yield from (_predicate_field(predicate) for predicate in parameters.predicates)
    elif isinstance(parameters, DetectRepetitionInput):
        yield parameters.topic_field


def _predicate_field(expression: str) -> str:
    match = _FIELD_PREFIX.match(expression.strip())
    if match is None:
        raise PlanValidationError("predicate contains an invalid semantic field")
    return match.group(1)


def _validate_semantic_field(
    field_name: str,
    manifests: Sequence[SourceManifest],
) -> None:
    normalized = field_name.strip()
    if normalized in _CANONICAL_FIELDS:
        return
    if any(token in normalized.split("_") for token in _SENSITIVE_TOKENS):
        raise PlanValidationError("PII or raw field use is not allowed")

    descriptors = [
        manifest.dimensions.get(normalized) or manifest.measures.get(normalized)
        for manifest in manifests
    ]
    known = [descriptor for descriptor in descriptors if descriptor is not None]
    if not known:
        raise PlanValidationError(f"unknown field {normalized}")
    if any(descriptor.pii_classification != "none" for descriptor in known):
        raise PlanValidationError(f"PII field {normalized} is not allowed")


__all__ = [
    "PlanValidationError",
    "validate_goal_against_request",
    "validate_plan",
    "validate_plan_revision",
]
