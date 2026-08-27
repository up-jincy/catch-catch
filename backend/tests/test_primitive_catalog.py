"""Fail-fast synchronization tests for the consolidated primitive catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from customer_signal.analytics import executor
from customer_signal.analytics.primitives import HANDLERS
from customer_signal.domain import analysis, facts
from customer_signal.domain.primitive_catalog import (
    PRIMITIVE_DEFINITIONS,
    all_capabilities,
    contract_document,
    dependency_arity_table,
    descriptions_ko,
    objectives,
    primitive_names,
    render_contract_json,
    required_canonical_metrics,
    required_metric_keys_table,
)
from customer_signal.domain.primitives import PRIMITIVE_INPUT_ADAPTER, PrimitiveInput
from customer_signal.domain.types import GenericPrimitiveName
from customer_signal.onboarding import adapter as onboarding_adapter
from customer_signal.synthetic import manifest as synthetic_manifest

_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "primitive-catalog.json"


def test_definitions_match_generic_primitive_name_literal() -> None:
    assert tuple(PRIMITIVE_DEFINITIONS) == get_args(GenericPrimitiveName.__value__)
    assert primitive_names() == tuple(PRIMITIVE_DEFINITIONS)


def test_definitions_match_handler_registry() -> None:
    assert tuple(HANDLERS) == tuple(PRIMITIVE_DEFINITIONS)
    for name, spec in HANDLERS.items():
        assert spec.input_type is PRIMITIVE_DEFINITIONS[name].input_type


def test_definitions_match_primitive_input_union() -> None:
    union_members = get_args(get_args(PrimitiveInput.__value__)[0])
    assert union_members == tuple(
        definition.input_type for definition in PRIMITIVE_DEFINITIONS.values()
    )
    adapter_mapping = PRIMITIVE_INPUT_ADAPTER.json_schema()["discriminator"]["mapping"]
    assert set(adapter_mapping) == set(PRIMITIVE_DEFINITIONS)
    for name, definition in PRIMITIVE_DEFINITIONS.items():
        literal = definition.input_type.model_fields["primitive"].annotation
        assert get_args(literal) == (name,)


def test_required_canonical_metrics_derive_from_catalog() -> None:
    assert facts._REQUIRED_CANONICAL_METRICS == required_canonical_metrics()
    assert facts._REQUIRED_CANONICAL_METRICS == {
        name: definition.required_metric_keys
        for name, definition in PRIMITIVE_DEFINITIONS.items()
    }
    for definition in PRIMITIVE_DEFINITIONS.values():
        assert definition.dynamic_metric == (not definition.required_metric_keys)
        if definition.dynamic_metric:
            assert len(definition.prompt_metric_keys) == 1
        else:
            assert definition.prompt_metric_keys == tuple(sorted(definition.required_metric_keys))


def test_dependency_arity_sites_derive_from_catalog() -> None:
    table = dependency_arity_table()
    assert executor._DEPENDENCY_ARITY == table
    assert analysis._DEPENDENCY_ARITY == table
    assert table == {
        name: definition.dependency_arity
        for name, definition in PRIMITIVE_DEFINITIONS.items()
    }


def test_capability_sets_derive_from_catalog() -> None:
    assert synthetic_manifest._ALL_CAPABILITIES == all_capabilities()
    assert onboarding_adapter._ALL_CAPABILITIES == all_capabilities()
    assert all_capabilities() == frozenset(PRIMITIVE_DEFINITIONS)


def test_prompt_tables_cover_every_primitive() -> None:
    assert tuple(required_metric_keys_table()) == tuple(PRIMITIVE_DEFINITIONS)
    assert tuple(objectives()) == tuple(PRIMITIVE_DEFINITIONS)
    assert set(descriptions_ko()) <= set(PRIMITIVE_DEFINITIONS)
    assert all(value for value in objectives().values())
    assert all(value for value in descriptions_ko().values())


def test_contract_file_matches_generated_document() -> None:
    on_disk = _CONTRACT_PATH.read_text(encoding="utf-8")
    assert on_disk == render_contract_json()
    document = json.loads(on_disk)
    assert document == contract_document()
    assert document["schema_version"] == 1
    names = [primitive["name"] for primitive in document["primitives"]]
    assert names == list(PRIMITIVE_DEFINITIONS)


def test_document_renderer_labels_cover_every_primitive() -> None:
    from customer_signal.domain.primitive_catalog import primitive_names
    from customer_signal.runtime.document_renderer import _PRIMITIVE_LABELS

    assert set(_PRIMITIVE_LABELS) == set(primitive_names())
