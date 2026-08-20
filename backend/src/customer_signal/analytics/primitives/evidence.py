"""Authorized masked-evidence retrieval primitive."""

from __future__ import annotations

from customer_signal.analytics.primitives.common import PrimitiveContext, metric
from customer_signal.domain.facts import (
    AnalysisMaskedEvidence,
    EvidencePayload,
    ProcessingStats,
)
from customer_signal.domain.primitives import GetEvidenceInput


def get_evidence(
    context: PrimitiveContext,
    parameters: GetEvidenceInput,
) -> EvidencePayload:
    authorized_ids = {
        event.evidence_id for event in context.events if event.source_id in context.scope.source_ids
    }
    requested = [
        evidence_id
        for fact in context.input_facts
        for evidence_id in fact.evidence_ids
        if evidence_id in authorized_ids
    ]
    requested = list(dict.fromkeys(requested))
    limit = min(parameters.limit, context.max_output_rows, context.max_evidence)
    requested = requested[:limit]
    records = (
        context.registry.get_evidence(requested, authorized_events=context.events)
        if requested
        else []
    )
    projected = [
        AnalysisMaskedEvidence(
            evidence_id=record.evidence_id,
            source_id=record.source_id,
            occurred_at=record.occurred_at,
            masked_customer_id=record.masked_customer_id,
            summary=record.summary,
        )
        for record in records
    ]
    return EvidencePayload(
        kind="get_evidence",
        records=projected,
        input_fact_ids=context.input_fact_ids,
        processing=ProcessingStats(
            scanned_events=len(context.events),
            matched_events=len(requested),
            returned_rows=len(projected),
        ),
        provenance=context.provenance,
        metrics=[metric("evidence_record_count", len(projected), unit="records")],
    )


__all__ = ["get_evidence"]
