"""Adversarial validation tests for generic analysis goals and plans."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from customer_signal.agent.contracts import RunRequest
from customer_signal.agent.plan_validator import (
    PlanValidationError,
    validate_goal_against_request,
    validate_plan,
    validate_plan_revision,
)
from customer_signal.domain.analysis import (
    AnalysisGoal,
    AnalysisPlan,
    AnalysisStep,
    ContinueAfterStep,
    ExpectedOutputSpec,
    MeasureSpec,
    PopulationSpec,
    StepLimits,
)
from customer_signal.domain.primitives import (
    CatalogSourcesInput,
    CompareSegmentsInput,
    ProfileEventsInput,
)
from customer_signal.domain.sources import (
    DimensionDescriptor,
    IdentityQualityDescriptor,
    MaskingPolicy,
    MeasureDescriptor,
    SourceManifest,
    TimeRange,
)


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)
LIMITS = StepLimits(
    max_input_events=100,
    max_output_rows=20,
    max_evidence=5,
    timeout_seconds=5,
)


def _manifest(*, capabilities: frozenset[str] | None = None) -> SourceManifest:
    return SourceManifest(
        source_id="voc",
        label="VOC",
        description="Masked support contacts",
        adapter_version="1",
        manifest_version="1",
        data_interval=TimeRange(start_at=NOW, end_at=NOW + timedelta(days=30)),
        refresh_cadence="daily",
        supported_event_types=frozenset({"voc"}),
        supported_topics=frozenset({"pricing"}),
        supported_outcomes=frozenset({"negative"}),
        dimensions={
            "channel": DimensionDescriptor(
                semantic_type="category",
                description="Channel",
                pii_classification="none",
            ),
            "email": DimensionDescriptor(
                semantic_type="identifier",
                description="Address",
                pii_classification="direct_identifier",
            ),
        },
        measures={
            "duration": MeasureDescriptor(
                semantic_type="number",
                description="Duration",
                unit="seconds",
            )
        },
        capabilities=capabilities
        or frozenset({"catalog_sources", "profile_events", "compare_segments"}),
        masking_policy=MaskingPolicy(rules={"email": "redact"}),
        identity_quality=IdentityQualityDescriptor(
            namespace="case", link_method="declared", confidence=1.0
        ),
    )


def _goal(**updates: object) -> AnalysisGoal:
    values: dict[str, object] = {
        "goal_id": "goal-1",
        "objective": "Profile feedback",
        "population": PopulationSpec(description="All customers"),
        "time_range": TimeRange(start_at=NOW, end_at=NOW + timedelta(days=1)),
        "source_ids": ["voc"],
        "measures": [
            MeasureSpec(
                metric_key="event_count",
                label="Events",
                aggregation="count",
                unit="events",
            )
        ],
        "output": "profile",
    }
    values.update(updates)
    return AnalysisGoal.model_validate(values)


def _raw_step(step_id: str, *, group_by: list[str] | None = None) -> AnalysisStep:
    return AnalysisStep(
        step_id=step_id,
        primitive="profile_events",
        parameters=ProfileEventsInput(
            primitive="profile_events", group_by=group_by or [], predicates=[]
        ),
        source_ids=["voc"],
        expected_output=ExpectedOutputSpec(
            payload_kind="profile_events", required_metric_keys=["event_count"]
        ),
        stop_condition=ContinueAfterStep(),
        limits=LIMITS,
    )


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="plan-1",
        revision=0,
        goal_id="goal-1",
        steps=[
            AnalysisStep(
                step_id="step-catalog",
                primitive="catalog_sources",
                parameters=CatalogSourcesInput(primitive="catalog_sources"),
                source_ids=["voc"],
                expected_output=ExpectedOutputSpec(
                    payload_kind="catalog_sources", required_metric_keys=["source_count"]
                ),
                stop_condition=ContinueAfterStep(),
                limits=LIMITS,
            ),
            _raw_step("step-profile-a"),
            _raw_step("step-profile-b"),
        ],
    )


def test_goal_cannot_expand_request_time_or_add_source() -> None:
    request = RunRequest(
        question="profile",
        start_at=NOW,
        end_at=NOW + timedelta(days=2),
        enabled_sources=["voc"],
    )
    validate_goal_against_request(_goal(), request)

    with pytest.raises(PlanValidationError, match="time range"):
        validate_goal_against_request(
            _goal(
                time_range=TimeRange(
                    start_at=NOW - timedelta(seconds=1), end_at=NOW + timedelta(days=1)
                )
            ),
            request,
        )
    with pytest.raises(PlanValidationError, match="source"):
        validate_goal_against_request(_goal(source_ids=["voc", "billing"]), request)


def test_plan_rejects_unknown_capability_field_and_pii() -> None:
    plan = _plan()
    validate_plan(plan, [_manifest()])

    with pytest.raises(PlanValidationError, match="capability"):
        validate_plan(plan, [_manifest(capabilities=frozenset({"catalog_sources"}))])

    unknown = plan.model_copy(deep=True)
    unknown.steps[1] = _raw_step("step-profile-a", group_by=["unknown"])
    with pytest.raises(PlanValidationError, match="unknown field"):
        validate_plan(unknown, [_manifest()])

    pii = plan.model_copy(deep=True)
    pii.steps[1] = _raw_step("step-profile-a", group_by=["email"])
    with pytest.raises(PlanValidationError, match="PII"):
        validate_plan(pii, [_manifest()])

    disabled = plan.model_copy(deep=True)
    disabled.steps[1].source_ids = ["billing"]
    with pytest.raises(PlanValidationError, match="unknown or disabled source"):
        validate_plan(disabled, [_manifest()])


def test_plan_rejects_dependency_arity_and_forward_reference_even_if_constructed() -> None:
    plan = _plan()
    invalid_compare = AnalysisStep(
        step_id="step-compare",
        primitive="compare_segments",
        parameters=CompareSegmentsInput(primitive="compare_segments", metric_key="event_count"),
        source_ids=["voc"],
        input_step_ids=["step-profile-a"],
        expected_output=ExpectedOutputSpec(
            payload_kind="compare_segments", required_metric_keys=["event_count_delta"]
        ),
        stop_condition=ContinueAfterStep(),
        limits=LIMITS,
    )
    forged = AnalysisPlan.model_construct(
        plan_id="plan-1",
        revision=0,
        goal_id="goal-1",
        steps=[plan.steps[0], invalid_compare, plan.steps[1]],
    )
    with pytest.raises(PlanValidationError, match="dependency"):
        validate_plan(forged, [_manifest()])


def test_plan_rejects_seven_steps_duplicates_and_mutated_limits() -> None:
    plan = _plan()
    seven = AnalysisPlan.model_construct(
        plan_id="plan-seven",
        revision=0,
        goal_id="goal-1",
        steps=[
            _raw_step("step-one"),
            _raw_step("step-two"),
            _raw_step("step-three"),
            _raw_step("step-four"),
            _raw_step("step-five"),
            _raw_step("step-six"),
            _raw_step("step-seven"),
        ],
    )
    with pytest.raises(PlanValidationError, match="bounded analysis plan"):
        validate_plan(seven, [_manifest()])

    duplicate = AnalysisPlan.model_construct(
        plan_id="plan-duplicate",
        revision=0,
        goal_id="goal-1",
        steps=[plan.steps[0], plan.steps[1], plan.steps[1]],
    )
    with pytest.raises(PlanValidationError, match="bounded analysis plan"):
        validate_plan(duplicate, [_manifest()])

    for field, value in (
        ("max_input_events", 10_001),
        ("max_output_rows", 101),
        ("max_evidence", 21),
        ("timeout_seconds", 41),
    ):
        over_limit = plan.model_copy(deep=True)
        setattr(over_limit.steps[1].limits, field, value)
        with pytest.raises(PlanValidationError, match="bounded analysis plan"):
            validate_plan(over_limit, [_manifest()])


def test_revision_preserves_completed_steps_and_increases_revision() -> None:
    previous = _plan()
    revised = previous.model_copy(deep=True, update={"revision": 1})
    validate_plan_revision(
        previous=previous,
        revised=revised,
        completed_step_ids={"step-catalog"},
        manifests=[_manifest()],
    )

    changed = revised.model_copy(deep=True)
    changed.steps[0].limits.max_output_rows = 10
    with pytest.raises(PlanValidationError, match="completed step is immutable"):
        validate_plan_revision(
            previous=previous,
            revised=changed,
            completed_step_ids={"step-catalog"},
            manifests=[_manifest()],
        )

    stale = previous.model_copy(deep=True)
    with pytest.raises(PlanValidationError, match="revision"):
        validate_plan_revision(
            previous=previous,
            revised=stale,
            completed_step_ids=set(),
            manifests=[_manifest()],
        )
