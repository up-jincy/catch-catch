"""Exact semantic binding of model-authored Claims to immutable analysis Facts."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import timedelta
from hashlib import sha256
from typing import cast

from customer_signal.domain.analysis import (
    AnalysisNote,
    AnalysisNoteDraft,
    ClaimDraft,
    FactRef,
    VerifiedClaim,
)
from customer_signal.domain.facts import (
    AnalysisFact,
    AnalysisMetricFact,
    SegmentComparisonPayload,
    SegmentCustomersPayload,
    validate_comparison_payload,
)
from customer_signal.domain.primitive_catalog import objectives


class ClaimValidationError(ValueError):
    """Raised before publication when a Claim is not exactly authorized by its Facts."""


_SENSITIVE_PATTERN = re.compile(
    r"(?:^|_)(?:raw|email|phone|address|password|secret|token|write|update|delete|insert|export)(?:_|$)",
    flags=re.IGNORECASE,
)
_UNSET_NEXT_STEP = object()


def validate_claim(
    draft: ClaimDraft,
    *,
    facts: Sequence[AnalysisFact],
    plan_revision: int = 0,
) -> VerifiedClaim:
    """Return a canonical server-owned Claim or fail without publishing prose."""

    if _SENSITIVE_PATTERN.search(draft.subject):
        raise ClaimValidationError("sensitive PII, raw export, or write Claim is not allowed")

    fact_by_id: dict[str, AnalysisFact] = {}
    for fact in facts:
        if fact.fact_id in fact_by_id:
            raise ClaimValidationError("Fact ledger contains duplicate fact_id values")
        fact_by_id[fact.fact_id] = fact
    referenced_fact_ids = {reference.fact_id for reference in draft.fact_refs}
    if len(referenced_fact_ids) != 1:
        raise ClaimValidationError("one Claim must bind to a single Fact")
    fact_id = next(iter(referenced_fact_ids))
    fact = fact_by_id.get(fact_id)
    if fact is None:
        raise ClaimValidationError("Claim references an unknown Fact")
    if isinstance(fact.payload, SegmentComparisonPayload):
        try:
            input_facts = [fact_by_id[fact_id] for fact_id in fact.payload.input_fact_ids]
        except KeyError as error:
            raise ClaimValidationError(
                "comparison Claim requires both ordered input Facts"
            ) from error
        try:
            validate_comparison_payload(fact.payload, input_facts)
        except ValueError as error:
            raise ClaimValidationError("comparison Claim input Fact binding is invalid") from error

    canonical_refs = [
        _validate_reference(reference, fact=fact, plan_revision=plan_revision)
        for reference in draft.fact_refs
    ]
    _validate_selector_shape(draft, canonical_refs)
    rendered = _validate_claim_semantics(draft, fact=fact, references=canonical_refs)
    canonical_subject = {
        "metric": draft.subject,
        "segment": "segment_id",
        "customer": "customer_id",
        "source": "source_id",
        "evidence": "evidence_id",
    }[draft.claim_type]
    canonical = {
        "claim_type": draft.claim_type,
        "subject": canonical_subject,
        "operator": draft.operator,
        "target": draft.target,
        "fact_refs": [reference.model_dump(mode="json") for reference in canonical_refs],
    }
    digest = sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    return VerifiedClaim(
        **canonical,
        claim_id=f"claim-{digest}",
        rendered_text=rendered,
    )


def validate_and_render_claim_draft(
    draft: ClaimDraft,
    facts: Sequence[AnalysisFact],
    *,
    plan_revision: int = 0,
) -> VerifiedClaim:
    """Compatibility spelling used by the analysis-loop composer."""

    return validate_claim(draft, facts=facts, plan_revision=plan_revision)


def render_verified_note(
    draft: AnalysisNoteDraft,
    fact: AnalysisFact,
    duration_ms: int,
    *,
    facts: Sequence[AnalysisFact] | None = None,
    next_step_id: str | None | object = _UNSET_NEXT_STEP,
    next_action: str = "현재 단계의 검증 결과를 기록했습니다.",
    plan_revision: int = 0,
) -> AnalysisNote:
    """Derive a public Note only from a server Fact and verified Claim drafts."""

    selected_step_id = (
        draft.next_step_id if next_step_id is _UNSET_NEXT_STEP else cast(str | None, next_step_id)
    )
    if draft.step_id != fact.step_id:
        raise ClaimValidationError("note draft step does not match server Fact")
    if selected_step_id == fact.step_id:
        raise ClaimValidationError("note next step cannot select the completed step")
    if not 0 <= duration_ms <= 40_000:
        raise ClaimValidationError("note duration exceeds the bounded Step timeout")

    fact_ledger = [fact] if facts is None else list(facts)
    if facts is not None:
        current_fact_matches = [item for item in fact_ledger if item.fact_id == fact.fact_id]
        if len(current_fact_matches) != 1 or current_fact_matches[0] != fact:
            raise ClaimValidationError("note Fact must appear exactly once in the Fact ledger")

    claims = [
        validate_claim(claim, facts=fact_ledger, plan_revision=plan_revision)
        for claim in draft.claims
    ]
    claim_ids = [claim.claim_id for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ClaimValidationError("note Claims must be unique")
    completed_at = fact.created_at
    started_at = completed_at - timedelta(milliseconds=duration_ms)
    note_operands = {
        "step_id": fact.step_id,
        "fact_id": fact.fact_id,
        "claim_ids": claim_ids,
        "next_step_id": selected_step_id,
        "next_action": next_action,
        "plan_revision": plan_revision,
    }
    note_digest = sha256(
        json.dumps(note_operands, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return AnalysisNote(
        note_id=f"note-{note_digest}",
        step_id=fact.step_id,
        objective=_objective_for_primitive(fact.primitive),
        fact_ids=[fact.fact_id],
        claims=claims,
        next_step_id=selected_step_id,
        next_action=next_action,
        limitations=list(draft.limitations),
        source_ids=list(fact.source_ids),
        result_ids=[fact.result_id],
        evidence_ids=list(fact.evidence_ids),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        plan_revision=plan_revision,
    )


def _validate_reference(
    reference: FactRef,
    *,
    fact: AnalysisFact,
    plan_revision: int,
) -> FactRef:
    if reference.plan_revision != plan_revision:
        raise ClaimValidationError("Claim references a stale plan revision")
    if reference.result_id is not None and reference.result_id != fact.result_id:
        raise ClaimValidationError("Claim result reference does not match the Fact")

    updates: dict[str, object] = {"result_id": fact.result_id}
    if reference.metric_key is not None:
        metric = _resolve_metric(reference, fact)
        if reference.label is not None and reference.label != metric.label:
            raise ClaimValidationError("Claim metric label does not match the Fact")
        if reference.unit is not None and reference.unit != metric.unit:
            raise ClaimValidationError("Claim metric unit does not match the Fact")
        if reference.dimensions is not None and reference.dimensions != metric.dimensions:
            raise ClaimValidationError("Claim metric dimensions do not match the Fact")
        updates.update(
            label=metric.label,
            unit=metric.unit,
            dimensions=metric.dimensions,
        )
    if reference.segment_id is not None:
        if not isinstance(fact.payload, SegmentCustomersPayload):
            raise ClaimValidationError("Segment reference requires a Segment Fact")
        if reference.segment_id != fact.payload.segment_id:
            raise ClaimValidationError("Claim Segment does not match the Fact")
    if reference.customer_id is not None and reference.customer_id not in fact.customer_ids:
        raise ClaimValidationError("Claim customer does not match the Fact")
    if reference.source_id is not None and reference.source_id not in fact.source_ids:
        raise ClaimValidationError("Claim source does not match the Fact")
    if reference.evidence_id is not None and reference.evidence_id not in fact.evidence_ids:
        raise ClaimValidationError("Claim evidence does not match the Fact")
    return reference.model_copy(update=updates)


def _resolve_metric(reference: FactRef, fact: AnalysisFact) -> AnalysisMetricFact:
    matches = [metric for metric in fact.metrics if metric.metric_key == reference.metric_key]
    if reference.dimensions is not None:
        matches = [metric for metric in matches if metric.dimensions == reference.dimensions]
    if len(matches) != 1:
        raise ClaimValidationError("Claim metric key and dimensions do not resolve exactly")
    return matches[0]


def _validate_selector_shape(draft: ClaimDraft, references: Sequence[FactRef]) -> None:
    selector_names = ("metric_key", "segment_id", "customer_id", "source_id", "evidence_id")
    expected_selector = {
        "metric": "metric_key",
        "segment": "segment_id",
        "customer": "customer_id",
        "source": "source_id",
        "evidence": "evidence_id",
    }[draft.claim_type]
    for reference in references:
        populated = {name for name in selector_names if getattr(reference, name) is not None}
        if populated != {expected_selector}:
            raise ClaimValidationError(
                "Claim FactRef selectors must exactly match the Claim semantic type"
            )


def _validate_claim_semantics(
    draft: ClaimDraft,
    *,
    fact: AnalysisFact,
    references: list[FactRef],
) -> str:
    if draft.claim_type == "metric":
        if len(references) != 1 or references[0].metric_key is None:
            raise ClaimValidationError("metric Claim requires exactly one metric FactRef")
        reference = references[0]
        metric = _resolve_metric(reference, fact)
        if draft.subject != metric.metric_key:
            raise ClaimValidationError("Claim subject does not match metric semantics")
        if type(draft.target) is not type(metric.value):
            raise ClaimValidationError("Claim target Python value type does not match metric")
        if not _comparison_holds(metric.value, draft.operator, draft.target):
            raise ClaimValidationError(
                "Claim operator and target are not supported by metric value"
            )
        return f"{metric.label}: {metric.value} {metric.unit}"

    selector_by_type = {
        "segment": ("segment_id", "segment_id"),
        "customer": ("customer_id", "customer_id"),
        "source": ("source_id", "source_id"),
        "evidence": ("evidence_id", "evidence_id"),
    }
    expected_subject, selector = selector_by_type[draft.claim_type]
    if draft.operator != "eq":
        raise ClaimValidationError(f"{draft.claim_type} Claim requires exact equality")
    if len(references) != 1:
        raise ClaimValidationError(f"{draft.claim_type} Claim requires exactly one FactRef")
    selected = getattr(references[0], selector)
    if draft.subject not in {expected_subject, selected}:
        raise ClaimValidationError(f"Claim subject does not match {draft.claim_type} semantics")
    if selected is None or type(draft.target) is not str or draft.target != selected:
        raise ClaimValidationError(f"Claim target does not match {draft.claim_type} Fact")
    return f"{expected_subject}: {selected}"


def _comparison_holds(left: int | float, operator: str, right: object) -> bool:
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if operator == "lt":
        return left < right  # type: ignore[operator]
    if operator == "lte":
        return left <= right  # type: ignore[operator]
    if operator == "gt":
        return left > right  # type: ignore[operator]
    if operator == "gte":
        return left >= right  # type: ignore[operator]
    return False


def _objective_for_primitive(primitive: str) -> str:
    labels: dict[str, str] = objectives()
    return labels[primitive]


__all__ = [
    "ClaimValidationError",
    "render_verified_note",
    "validate_and_render_claim_draft",
    "validate_claim",
]
