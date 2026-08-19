from __future__ import annotations

import pytest
from pydantic import ValidationError

from customer_signal.agent.contracts import (
    RunFacts,
    RunRequest,
    RunnerOutcome,
    UnsupportedClaimError,
)
from customer_signal.agent.fixture import FixtureRunner
from customer_signal.agent.validator import validate_report
from customer_signal.analytics.service import AnalyticsService
from customer_signal.data.repository import DuckDBRepository
from customer_signal.domain.reports import Finding, Recommendation
from customer_signal.mcp_server import create_mcp_server


START_AT = "2026-07-20T00:00:00+09:00"
END_AT = "2026-08-19T00:00:00+09:00"
ALL_SOURCES = ["search_history", "search_feedback", "voc"]


@pytest.fixture
async def valid_outcome(repository: DuckDBRepository) -> RunnerOutcome:
    runner = FixtureRunner(create_mcp_server(AnalyticsService(repository)))
    return await runner.run(
        RunRequest(
            question="검색 실패 뒤 고객센터에 문의한 고객을 분석해 줘",
            start_at=START_AT,
            end_at=END_AT,
            enabled_sources=ALL_SOURCES,
        ),
        emit=lambda _event: None,
    )


async def test_validator_accepts_actual_run_facts(valid_outcome: RunnerOutcome) -> None:
    validated = validate_report(valid_outcome.report, valid_outcome.facts)

    assert validated is valid_outcome.report
    assert set(valid_outcome.facts.allowed_evidence_ids) == {
        *(
            evidence_id
            for customer in valid_outcome.report.ranked_customers
            for evidence_id in customer.evidence_ids
        ),
        *(event.evidence_id for event in valid_outcome.report.representative_journeys),
    }
    assert len(valid_outcome.facts.fetched_evidence_ids) == 1
    assert valid_outcome.facts.fetched_evidence_ids <= (valid_outcome.facts.allowed_evidence_ids)
    assert set(valid_outcome.facts.evidence_source_facts) == set(
        valid_outcome.facts.fetched_evidence_ids
    )
    assert set(valid_outcome.facts.evidence_customer_facts) == set(
        valid_outcome.facts.fetched_evidence_ids
    )
    fetched_id = next(iter(valid_outcome.facts.fetched_evidence_ids))
    selected_event = next(
        event
        for event in valid_outcome.report.representative_journeys
        if event.evidence_id == fetched_id
    )
    assert valid_outcome.facts.evidence_source_facts[fetched_id] == selected_event.source_id
    assert (
        valid_outcome.facts.evidence_customer_facts[fetched_id]
        == valid_outcome.report.ranked_customers[0].customer_id
    )


async def test_validator_rejects_fabricated_customer(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.ranked_customers[0].customer_id = "CUST-FABRICATED"

    with pytest.raises(UnsupportedClaimError, match="customer"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_fabricated_evidence(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.findings[0].evidence_ids = ["EVD-FABRICATED"]

    with pytest.raises(UnsupportedClaimError, match="evidence"):
        validate_report(invalid, valid_outcome.facts)


@pytest.mark.parametrize("claim_type", ["finding", "recommendation"])
async def test_validator_rejects_globally_allowed_but_unfetched_claim_evidence(
    valid_outcome: RunnerOutcome,
    claim_type: str,
) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    unfetched_evidence_id = next(
        evidence_id
        for evidence_id in valid_outcome.facts.allowed_evidence_ids
        if evidence_id not in valid_outcome.facts.fetched_evidence_ids
    )
    if claim_type == "finding":
        invalid.findings[0].evidence_ids = [unfetched_evidence_id]
    else:
        invalid.recommendations[0].evidence_ids = [unfetched_evidence_id]

    with pytest.raises(UnsupportedClaimError, match="fetched evidence"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_fabricated_metric_result(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.metrics[0].result_id = "match_journey_pattern:fabricated"

    with pytest.raises(UnsupportedClaimError, match="metric"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_fabricated_metric_value(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.metrics[0].value = 7

    with pytest.raises(UnsupportedClaimError, match="metric"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_wrong_semantic_metric_even_when_value_exists_in_result(
    valid_outcome: RunnerOutcome,
) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    metric = invalid.metrics[0]
    allowed_metrics = valid_outcome.facts.allowed_metrics_by_result[metric.result_id]
    metric.value = next(
        supported.value
        for supported in allowed_metrics
        if type(supported.value) is type(metric.value) and supported.value != metric.value
    )

    with pytest.raises(UnsupportedClaimError, match="metric"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_source_outside_run(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.sources_used = ["billing"]  # type: ignore[list-item]

    with pytest.raises(UnsupportedClaimError, match="source"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_fabricated_risk_score(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.ranked_customers[0].risk_score -= 1

    with pytest.raises(UnsupportedClaimError, match="ranked customer"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_cross_customer_signal_evidence(
    valid_outcome: RunnerOutcome,
) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    other_customer_evidence = invalid.ranked_customers[1].evidence_ids[0]
    assert other_customer_evidence in valid_outcome.facts.allowed_evidence_ids
    invalid.ranked_customers[0].signals[0].evidence_ids = [other_customer_evidence]

    with pytest.raises(UnsupportedClaimError, match="ranked customer"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_cross_customer_journey_evidence(
    valid_outcome: RunnerOutcome,
) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    other_customer_evidence = invalid.ranked_customers[1].evidence_ids[0]
    assert other_customer_evidence in valid_outcome.facts.allowed_evidence_ids
    invalid.representative_journeys[0].evidence_id = other_customer_evidence

    with pytest.raises(UnsupportedClaimError, match="journey"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_evidence_free_finding(valid_outcome: RunnerOutcome) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.findings = [
        Finding(
            title="unsupported",
            description="unsupported",
            confidence="low",
            evidence_ids=[],
        )
    ]

    with pytest.raises(UnsupportedClaimError, match="finding"):
        validate_report(invalid, valid_outcome.facts)


async def test_validator_rejects_evidence_free_recommendation(
    valid_outcome: RunnerOutcome,
) -> None:
    invalid = valid_outcome.report.model_copy(deep=True)
    invalid.recommendations = [
        Recommendation(
            action_id="further_analysis",
            title="unsupported",
            reason="unsupported",
            evidence_ids=[],
        )
    ]

    with pytest.raises(UnsupportedClaimError, match="recommendation"):
        validate_report(invalid, valid_outcome.facts)


def _empty_facts_values() -> dict[str, object]:
    tool_result_ids = {
        "catalog_sources": "catalog_sources:one",
        "aggregate_events": "aggregate_events:two",
        "match_journey_pattern": "match_journey_pattern:three",
        "rank_customers": "rank_customers:four",
    }
    return {
        "tool_result_ids": tool_result_ids,
        "allowed_customer_ids": frozenset(),
        "allowed_evidence_ids": frozenset(),
        "allowed_sources": frozenset(ALL_SOURCES),
        "allowed_metrics_by_result": {
            result_id: ({"label": "count", "value": 0, "unit": None},)
            for result_id in tool_result_ids.values()
        },
        "ranked_customer_facts": {},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: values["tool_result_ids"].update(  # type: ignore[union-attr]
            {"rank_customers": "match_journey_pattern:three"}
        ),
        lambda values: values["tool_result_ids"].update(  # type: ignore[union-attr]
            {"rank_customers": "aggregate_events:not-ranked"}
        ),
        lambda values: values["allowed_metrics_by_result"].pop(  # type: ignore[union-attr]
            "rank_customers:four"
        ),
    ],
)
def test_run_facts_rejects_ambiguous_or_unbound_result_provenance(mutate) -> None:
    values = _empty_facts_values()
    mutate(values)

    with pytest.raises(ValidationError, match="result_id"):
        RunFacts.model_validate(values)


def test_run_facts_rejects_duplicate_metric_semantics_for_one_result() -> None:
    values = _empty_facts_values()
    metrics = values["allowed_metrics_by_result"]
    assert isinstance(metrics, dict)
    metrics["match_journey_pattern:three"] = (
        {"label": "완전한 Journey 패턴 고객 수", "value": 0, "unit": "명"},
        {"label": "완전한 Journey 패턴 고객 수", "value": 1, "unit": "명"},
    )

    with pytest.raises(ValidationError, match="metric"):
        RunFacts.model_validate(values)
