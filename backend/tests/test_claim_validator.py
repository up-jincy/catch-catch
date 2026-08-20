"""Fail-closed Claim validation and verified Note rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from customer_signal.agent.claim_validator import (
    ClaimValidationError,
    render_verified_note,
    validate_claim,
)
from customer_signal.domain.analysis import AnalysisNoteDraft, ClaimDraft, FactRef
from customer_signal.domain.facts import (
    AnalysisMetricDelta,
    AnalysisMetricFact,
    FactProvenance,
    ProcessingStats,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
    build_fact,
)
from customer_signal.domain.sources import EventScope


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)
SCOPE = EventScope(
    start_at=NOW,
    end_at=NOW + timedelta(days=1),
    source_ids=["voc"],
    max_events=100,
)


def _fact(*, value: int = 6):
    metric = AnalysisMetricFact(
        metric_key="segment_customer_count",
        label="Segment customers",
        value=value,
        unit="customers",
    )
    payload = SegmentCustomersPayload(
        kind="segment_customers",
        input_fact_ids=[],
        processing=ProcessingStats(scanned_events=10, matched_events=value, returned_rows=value),
        provenance=FactProvenance(
            scope=SCOPE,
            source_ids=["voc"],
            adapter_versions={"voc": "1"},
            manifest_versions={"voc": "1"},
            dataset_version="test-1",
        ),
        metrics=[metric],
        segment_id="segment-negative",
        customer_ids=[f"customer-{index}" for index in range(value)],
        predicate_counts={"negative": value},
    )
    return build_fact(
        fact_id="fact-segment",
        step_id="step-segment",
        primitive="segment_customers",
        result_id="result-segment",
        payload=payload,
        scope=SCOPE,
        created_at=NOW,
    )


def _metric_claim(**updates: object) -> ClaimDraft:
    values: dict[str, object] = {
        "claim_type": "metric",
        "subject": "segment_customer_count",
        "operator": "eq",
        "target": 6,
        "fact_refs": [
            FactRef(
                fact_id="fact-segment",
                metric_key="segment_customer_count",
                label="Segment customers",
                unit="customers",
                plan_revision=2,
            )
        ],
    }
    values.update(updates)
    return ClaimDraft.model_validate(values)


def test_claim_exactly_binds_metric_key_label_value_type_unit_and_revision() -> None:
    fact = _fact()
    verified = validate_claim(_metric_claim(), facts=[fact], plan_revision=2)
    replay = validate_claim(_metric_claim(), facts=[fact], plan_revision=2)
    assert verified.claim_id == replay.claim_id
    assert verified.fact_refs[0].label == "Segment customers"

    attacks = [
        _metric_claim(subject="revenue"),
        _metric_claim(target=6.0),
        _metric_claim(operator="gt"),
        _metric_claim(
            fact_refs=[
                FactRef(
                    fact_id="fact-segment",
                    metric_key="segment_customer_count",
                    label="Wrong label",
                    unit="customers",
                    plan_revision=2,
                )
            ]
        ),
        _metric_claim(
            fact_refs=[
                FactRef(
                    fact_id="fact-segment",
                    metric_key="segment_customer_count",
                    label="Segment customers",
                    unit="events",
                    plan_revision=2,
                )
            ]
        ),
        _metric_claim(
            fact_refs=[
                FactRef(
                    fact_id="fact-segment",
                    metric_key="segment_customer_count",
                    plan_revision=1,
                )
            ]
        ),
    ]
    for attack in attacks:
        with pytest.raises(ClaimValidationError):
            validate_claim(attack, facts=[fact], plan_revision=2)


@pytest.mark.parametrize(
    "claim",
    [
        ClaimDraft(
            claim_type="segment",
            subject="segment_id",
            operator="eq",
            target="segment-forged",
            fact_refs=[
                FactRef(fact_id="fact-segment", segment_id="segment-forged", plan_revision=2)
            ],
        ),
        ClaimDraft(
            claim_type="customer",
            subject="customer_id",
            operator="eq",
            target="customer-forged",
            fact_refs=[
                FactRef(fact_id="fact-segment", customer_id="customer-forged", plan_revision=2)
            ],
        ),
        ClaimDraft(
            claim_type="source",
            subject="source_id",
            operator="eq",
            target="billing",
            fact_refs=[FactRef(fact_id="fact-segment", source_id="billing", plan_revision=2)],
        ),
        ClaimDraft(
            claim_type="evidence",
            subject="evidence_id",
            operator="eq",
            target="evidence-forged",
            fact_refs=[
                FactRef(fact_id="fact-segment", evidence_id="evidence-forged", plan_revision=2)
            ],
        ),
    ],
)
def test_claim_rejects_forged_segment_customer_source_and_evidence(claim: ClaimDraft) -> None:
    with pytest.raises(ClaimValidationError):
        validate_claim(claim, facts=[_fact()], plan_revision=2)


def test_claim_rejects_cross_fact_and_sensitive_operations() -> None:
    other = _fact().model_copy(update={"fact_id": "fact-other", "result_id": "result-other"})
    cross = _metric_claim(
        fact_refs=[
            FactRef(
                fact_id="fact-segment",
                metric_key="segment_customer_count",
                plan_revision=2,
            ),
            FactRef(
                fact_id="fact-other",
                metric_key="segment_customer_count",
                plan_revision=2,
            ),
        ]
    )
    with pytest.raises(ClaimValidationError, match="single Fact"):
        validate_claim(cross, facts=[_fact(), other], plan_revision=2)

    mixed = _metric_claim(
        fact_refs=[
            FactRef(
                fact_id="fact-segment",
                metric_key="segment_customer_count",
                customer_id="customer-0",
                plan_revision=2,
            )
        ]
    )
    with pytest.raises(ClaimValidationError, match="selectors"):
        validate_claim(mixed, facts=[_fact()], plan_revision=2)

    for subject in ("raw_email_export", "write_customer", "delete_record"):
        unsafe = _metric_claim(subject=subject)
        with pytest.raises(ClaimValidationError, match="sensitive"):
            validate_claim(unsafe, facts=[_fact()], plan_revision=2)


def test_comparison_claim_revalidates_ordered_input_fact_metrics() -> None:
    baseline = _fact(value=6)
    comparison = _fact(value=8).model_copy(
        update={"fact_id": "fact-comparison-input", "result_id": "result-comparison-input"}
    )
    payload = SegmentComparisonPayload(
        kind="compare_segments",
        input_fact_ids=[baseline.fact_id, comparison.fact_id],
        processing=ProcessingStats(scanned_events=2, matched_events=2, returned_rows=1),
        provenance=baseline.payload.provenance,
        metrics=[
            AnalysisMetricFact(
                metric_key="segment_customer_count_delta",
                label="Segment customer delta",
                value=2,
                unit="customers",
            )
        ],
        requested_metric_key="segment_customer_count_delta",
        baseline_fact_id=baseline.fact_id,
        comparison_fact_id=comparison.fact_id,
        deltas=[
            AnalysisMetricDelta(
                metric_key="segment_customer_count",
                baseline=6,
                comparison=8,
                delta=2,
                unit="customers",
            )
        ],
    )
    comparison_fact = build_fact(
        fact_id="fact-comparison",
        step_id="step-comparison",
        primitive="compare_segments",
        result_id="result-comparison",
        payload=payload,
        scope=SCOPE,
        created_at=NOW,
        input_facts=[baseline, comparison],
    )
    claim = ClaimDraft(
        claim_type="metric",
        subject="segment_customer_count_delta",
        operator="eq",
        target=2,
        fact_refs=[
            FactRef(
                fact_id=comparison_fact.fact_id,
                metric_key="segment_customer_count_delta",
                label="Segment customer delta",
                unit="customers",
                plan_revision=2,
            )
        ],
    )

    verified = validate_claim(
        claim,
        facts=[comparison_fact, baseline, comparison],
        plan_revision=2,
    )
    assert verified.target == 2

    with pytest.raises(ClaimValidationError, match="both ordered input Facts"):
        validate_claim(claim, facts=[comparison_fact], plan_revision=2)

    forged_comparison = comparison.model_copy(
        update={
            "metrics": [
                AnalysisMetricFact(
                    metric_key="segment_customer_count",
                    label="Segment customers",
                    value=9,
                    unit="customers",
                )
            ]
        }
    )
    with pytest.raises(ClaimValidationError, match="input Fact binding"):
        validate_claim(
            claim,
            facts=[comparison_fact, baseline, forged_comparison],
            plan_revision=2,
        )


def test_render_note_publishes_only_verified_claims_and_server_fact_metadata() -> None:
    fact = _fact()
    draft = AnalysisNoteDraft(
        step_id="step-segment",
        claims=[_metric_claim()],
        next_step_id=None,
        limitations=["Only the selected time range was analyzed."],
    )
    note = render_verified_note(draft, fact, duration_ms=25, plan_revision=2)
    assert note.fact_ids == [fact.fact_id]
    assert note.result_ids == [fact.result_id]
    assert note.source_ids == fact.source_ids
    assert note.evidence_ids == fact.evidence_ids
    assert note.claims[0].claim_id.startswith("claim-")
    assert note.duration_ms == 25

    forged = draft.model_copy(update={"step_id": "step-other"})
    with pytest.raises(ClaimValidationError, match="step"):
        render_verified_note(forged, fact, duration_ms=25, plan_revision=2)
