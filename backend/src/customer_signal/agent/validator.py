"""Semantic validation of reports against facts returned during the same run."""

from __future__ import annotations

from customer_signal.agent.contracts import RunFacts, UnsupportedClaimError
from customer_signal.domain.reports import InsightReport, Signal


def _raise(message: str) -> None:
    raise UnsupportedClaimError(message)


def _validate_evidence_ids(
    evidence_ids: list[str],
    *,
    allowed: frozenset[str],
    context: str,
) -> None:
    unknown = set(evidence_ids) - allowed
    if unknown:
        _raise(f"{context} references unsupported evidence")


def _signal_exists_in_ranked_facts(signal: Signal, facts: RunFacts) -> bool:
    return any(
        signal == supported
        for customer in facts.ranked_customer_facts.values()
        for supported in customer.signals
    )


def validate_report(report: InsightReport, facts: RunFacts) -> InsightReport:
    """Return the report only when every externally visible claim is authorized."""

    result_ids = set(facts.tool_result_ids.values())
    for metric in report.metrics:
        supported_metrics = facts.allowed_metrics_by_result.get(metric.result_id)
        if metric.result_id not in result_ids or supported_metrics is None:
            _raise("metric references an unsupported result")
        if not any(
            metric.label == supported.label
            and type(metric.value) is type(supported.value)
            and metric.value == supported.value
            and metric.unit == supported.unit
            for supported in supported_metrics
        ):
            _raise("metric does not match an exact semantic Tool fact")

    if len(report.sources_used) != len(set(report.sources_used)):
        _raise("source list contains duplicates")
    if not set(report.sources_used) <= facts.allowed_sources:
        _raise("source was not enabled for this run")
    if not set(report.scope.enabled_sources) <= facts.allowed_sources:
        _raise("scope source was not enabled for this run")

    for finding in report.findings:
        if not finding.evidence_ids:
            _raise("finding must include evidence")
        _validate_evidence_ids(
            finding.evidence_ids,
            allowed=facts.fetched_evidence_ids,
            context="finding fetched evidence",
        )

    for recommendation in report.recommendations:
        if not recommendation.evidence_ids:
            _raise("recommendation must include evidence")
        _validate_evidence_ids(
            recommendation.evidence_ids,
            allowed=facts.fetched_evidence_ids,
            context="recommendation fetched evidence",
        )

    for customer in report.ranked_customers:
        if customer.customer_id not in facts.allowed_customer_ids:
            _raise("ranked customer is not allowed")
        supported = facts.ranked_customer_facts.get(customer.customer_id)
        if supported is None or customer != supported:
            _raise("ranked customer does not match exact tool facts")
        _validate_evidence_ids(
            customer.evidence_ids,
            allowed=facts.allowed_evidence_ids,
            context="ranked customer",
        )
        for signal in customer.signals:
            _validate_evidence_ids(
                signal.evidence_ids,
                allowed=facts.allowed_evidence_ids,
                context="ranked customer signal",
            )

    for contribution in report.signal_contributions:
        if contribution.source_id not in facts.allowed_sources:
            _raise("signal contribution uses an unsupported source")
        if contribution.score != sum(signal.score for signal in contribution.signals):
            _raise("signal contribution score is unsupported")
        for signal in contribution.signals:
            if not _signal_exists_in_ranked_facts(signal, facts):
                _raise("signal contribution is absent from ranked customer facts")
            if facts.signal_source_facts.get(signal.code) != contribution.source_id:
                _raise("signal contribution source does not match its signal")
            _validate_evidence_ids(
                signal.evidence_ids,
                allowed=facts.allowed_evidence_ids,
                context="signal contribution",
            )

    if report.representative_journeys and not report.representative_journey_ids:
        _raise("journey result identifier is required")
    if not set(report.representative_journey_ids) <= facts.representative_journey_result_ids:
        _raise("journey result identifier is unsupported")
    for event in report.representative_journeys:
        supported_event = facts.journey_event_facts.get(event.event_id)
        if supported_event is None or event != supported_event:
            _raise("journey event does not match exact tool facts")
        if event.source_id not in facts.allowed_sources:
            _raise("journey event uses an unsupported source")
        _validate_evidence_ids(
            [event.evidence_id],
            allowed=facts.allowed_evidence_ids,
            context="journey",
        )

    return report


__all__ = ["validate_report"]
