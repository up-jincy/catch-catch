"""Mapping-spec draft generation: name/type heuristics by default, Gemini optionally."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import TypeAdapter

from customer_signal.onboarding.profiler import ColumnProfile, TableProfile
from customer_signal.onboarding.spec import (
    DimensionSpec,
    FieldRule,
    IdentitySpec,
    MeasureSpec,
    SourceMappingSpec,
)

_CATEGORY_MAX_DISTINCT = 20
_MAX_DIMENSIONS = 10
_TIMESTAMP_NAMES = re.compile(r"time|date|_at$|^at_", re.IGNORECASE)
_CUSTOMER_NAMES = re.compile(r"customer|user|member|account|subscriber", re.IGNORECASE)
_DIRECT_PII_NAMES = re.compile(r"phone|email|name|address|birth|ssn", re.IGNORECASE)
_OUTCOME_NAMES = re.compile(r"outcome|status|result|state", re.IGNORECASE)
_ACTION_NAMES = re.compile(r"action|activity|step|behavior", re.IGNORECASE)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return (slug or "onboarded")[:64]


def heuristic_spec(
    profile: TableProfile,
    *,
    source_id: str,
    label: str,
    description: str,
) -> SourceMappingSpec:
    """Deterministic best-effort draft; a human reviews and edits it before register."""

    columns = profile.columns
    timestamp = _pick(columns, lambda c: "datetime" in c.dtype or _TIMESTAMP_NAMES.search(c.name))
    if timestamp is None:
        raise ValueError("no timestamp-like column found; set timestamp_column manually")
    used = {timestamp.name}

    customer = _pick(
        columns,
        lambda c: c.name not in used and c.dtype == "str" and _CUSTOMER_NAMES.search(c.name),
    ) or _pick(
        columns,
        lambda c: (
            c.name not in used and c.dtype == "str" and c.distinct_count > _CATEGORY_MAX_DISTINCT
        ),
    )
    if customer is None:
        raise ValueError("no customer-like column found; set identity.customer_column manually")
    used.add(customer.name)

    action = _pick(columns, lambda c: c.name not in used and _ACTION_NAMES.search(c.name))
    outcome = _pick(columns, lambda c: c.name not in used and _OUTCOME_NAMES.search(c.name))
    for picked in (action, outcome):
        if picked is not None:
            used.add(picked.name)
    topic = _pick(
        columns,
        lambda c: (
            c.name not in used
            and c.dtype == "str"
            and 1 <= c.distinct_count <= _CATEGORY_MAX_DISTINCT
        ),
    )
    if topic is not None:
        used.add(topic.name)

    dimensions: dict[str, DimensionSpec] = {}
    measures: dict[str, MeasureSpec] = {}
    for column in columns:
        if column.name in used:
            continue
        if column.dtype in ("int", "float") and not column.name.endswith("_id"):
            measures[_slug(column.name)] = MeasureSpec(
                column=column.name,
                semantic_type="integer" if column.dtype == "int" else "number",
                description=f"Raw column {column.name}.",
                unit="value",
            )
            continue
        if len(dimensions) >= _MAX_DIMENSIONS:
            continue
        if column.dtype == "bool":
            semantic_type = "boolean"
        elif column.name.endswith("_id"):
            semantic_type = "identifier"
        elif column.distinct_count <= _CATEGORY_MAX_DISTINCT:
            semantic_type = "category"
        else:
            semantic_type = "text"
        if _DIRECT_PII_NAMES.search(column.name):
            pii, masking = "direct_identifier", "redact"
        elif semantic_type == "identifier":
            pii, masking = "quasi_identifier", "hash"
        else:
            pii, masking = "none", None
        dimensions[_slug(column.name)] = DimensionSpec(
            column=column.name,
            semantic_type=semantic_type,
            description=f"Raw column {column.name}.",
            pii_classification=pii,
            masking=masking,
        )

    return SourceMappingSpec(
        source_id=_slug(source_id),
        label=label,
        description=description,
        timestamp_column=timestamp.name,
        event_type=FieldRule(const=_slug(source_id)),
        action=FieldRule(column=action.name) if action else FieldRule(const="event"),
        topic=FieldRule(column=topic.name) if topic else FieldRule(const=_slug(source_id)),
        outcome=FieldRule(column=outcome.name) if outcome else FieldRule(const="recorded"),
        identity=IdentitySpec(namespace=f"{_slug(source_id)}_entry", customer_column=customer.name),
        dimensions=dimensions,
        measures=measures,
    )


def _pick(columns: list[ColumnProfile], predicate) -> ColumnProfile | None:
    return next((column for column in columns if predicate(column)), None)


async def gemini_spec(
    profile: TableProfile,
    *,
    source_id: str,
    label: str,
    description: str,
    api_key: str,
    model_name: str = "gemini-3.7-flash",
) -> SourceMappingSpec:
    """Ask Gemini for a spec draft; fall back to the caller on validation failure."""

    from langchain_google_genai import ChatGoogleGenerativeAI

    adapter: TypeAdapter[Any] = TypeAdapter(SourceMappingSpec)
    schema = adapter.json_schema()
    schema["title"] = "SourceMappingSpec"
    model = ChatGoogleGenerativeAI(model=model_name, api_key=api_key, retries=0)
    prompt = json.dumps(
        {
            "task": (
                "다음 raw 테이블 프로파일을 캐노니컬 고객 이벤트 소스로 매핑하는 "
                "SourceMappingSpec을 작성하라. 컬럼 의미를 추론해 event_type(패턴 "
                "^[a-z][a-z0-9_]{1,63}$)/action/topic/outcome을 column·const·value_map으로 "
                "매핑하고, 고객 식별 컬럼을 identity.customer_column에 지정하고, 나머지 "
                "컬럼을 dimensions(문자/불리언, PII 분류·masking 포함)와 measures(숫자)로 "
                "분류하라. 확신 없는 PII는 quasi_identifier+hash로 보수적으로 분류하라."
            ),
            "source_id": _slug(source_id),
            "label": label,
            "description": description,
            "profile": profile.model_dump(mode="json"),
            "target_schema": schema,
        },
        ensure_ascii=False,
    )
    raw = await model.with_structured_output(schema, method="json_schema").ainvoke(prompt)
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    return adapter.validate_json(raw)


__all__ = ["gemini_spec", "heuristic_spec"]
