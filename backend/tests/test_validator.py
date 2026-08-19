from __future__ import annotations

import pytest

from customer_signal.agent.contracts import RunRequest, RunnerOutcome, UnsupportedClaimError
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

