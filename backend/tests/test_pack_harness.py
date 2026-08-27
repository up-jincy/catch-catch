"""Both registered Packs pass the same AnalysisPackHarness contract."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from customer_signal.agent.generic_fixture import NEGATIVE_TOPIC_QUESTION
from customer_signal.api import _default_dependencies
from customer_signal.config import Settings
from customer_signal.journal.memory import InMemoryEventJournal
from customer_signal.packs.harness import assert_pack_contract
from customer_signal.packs.kernel import PackKernel
from customer_signal.packs.registry import AnalysisPackRegistry
from customer_signal.packs.source_overview import SourceOverviewPack

SOURCES = [
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
]


@pytest.fixture(scope="module")
def dependencies(tmp_path_factory):
    tmp_path: Path = tmp_path_factory.mktemp("pack-harness")
    settings = Settings(
        agent_mode="fixture",
        database_path=tmp_path / "customer-signal.duckdb",
        artifact_directory=tmp_path / "artifacts",
        onboarded_sources_dir=tmp_path / "onboarded-sources",
        _env_file=None,
    )
    return _default_dependencies(settings)


async def test_customer_signal_pack_passes_the_harness(dependencies) -> None:
    pack = dependencies.packs.get("customer_signal")
    report = await assert_pack_contract(
        pack,
        {
            "question": NEGATIVE_TOPIC_QUESTION,
            "start_at": "2026-07-20T00:00:00+09:00",
            "end_at": "2026-08-19T00:00:00+09:00",
            "enabled_sources": SOURCES,
        },
        options={"mode": "fixture"},
    )
    assert report.status == "completed"
    assert report.event_kinds[0] == "run.opened"
    assert report.event_kinds[-1] == "run.completed"
    assert "customer_signal.report.v1" in report.artifact_schema_ids
    assert report.intent_count > 0


async def test_source_overview_pack_passes_the_harness(dependencies) -> None:
    pack = SourceOverviewPack(dependencies.registry)
    report = await assert_pack_contract(
        pack,
        {"question": "사용 가능한 Source를 요약해줘.", "enabled_sources": SOURCES},
    )
    assert report.status == "completed"
    assert report.artifact_schema_ids == [
        "source_overview.goal.v1",
        "source_overview.plan.v1",
        "source_overview.fact.v1",
        "source_overview.report.v1",
    ]
    assert report.intent_count > 0


def test_second_pack_registers_with_one_registry_line(dependencies) -> None:
    registry = AnalysisPackRegistry(
        [
            dependencies.packs.get("customer_signal"),
            SourceOverviewPack(dependencies.registry),
        ]
    )
    assert registry.pack_ids() == ("customer_signal", "source_overview")


async def test_source_overview_unknown_source_maps_to_a_public_error() -> None:
    class EmptyRegistry:
        def manifests(self, source_ids):
            raise LookupError("unknown source")

    kernel = PackKernel(InMemoryEventJournal())
    result = await kernel.run(
        SourceOverviewPack(EmptyRegistry()),
        {"question": "요약", "enabled_sources": ["missing_source"]},
        run_id=uuid4(),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "unknown_source"
