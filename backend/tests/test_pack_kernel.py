"""Pack Kernel and Registry contract tests using a scripted demo Pack."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from customer_signal.journal.events import CanonicalRunEvent
from customer_signal.journal.journal import UnknownRunError
from customer_signal.journal.memory import InMemoryEventJournal
from customer_signal.packs.contracts import (
    ActivityDraft,
    AnalysisPackSpec,
    ArtifactSchema,
    FactDraft,
    GoalDraft,
    InteractionDraft,
    NoteDraft,
    OutcomeDraft,
    PackContext,
    PackDegraded,
    PackDomainError,
    PackEmission,
    PlanDraft,
    ReportDraft,
)
from customer_signal.packs.kernel import PackInputError, PackKernel
from customer_signal.packs.registry import AnalysisPackRegistry, PackRegistrationError


class DemoContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DemoInput(DemoContract):
    question: str


class DemoGoal(DemoContract):
    title: str


class DemoPlan(DemoContract):
    steps: list[str]


class DemoFact(DemoContract):
    metric: int


class DemoNote(DemoContract):
    text: str


class DemoReport(DemoContract):
    headline: str


def demo_spec(pack_id: str = "demo_pack") -> AnalysisPackSpec:
    return AnalysisPackSpec(
        pack_id=pack_id,
        pack_version="1.0.0",
        title_ko="데모 분석",
        description_ko="Kernel 계약 테스트용 스크립트 Pack입니다.",
        input_schema_id=f"{pack_id}.input.v1",
        artifact_schemas=(
            ArtifactSchema(kind="goal", schema_id=f"{pack_id}.goal.v1", model=DemoGoal),
            ArtifactSchema(kind="plan", schema_id=f"{pack_id}.plan.v1", model=DemoPlan),
            ArtifactSchema(kind="fact", schema_id=f"{pack_id}.fact.v1", model=DemoFact),
            ArtifactSchema(kind="note", schema_id=f"{pack_id}.note.v1", model=DemoNote),
            ArtifactSchema(
                kind="report", schema_id=f"{pack_id}.report.v1", model=DemoReport
            ),
        ),
        required_catalog_keys=("Card", "Metric"),
    )


HAPPY_SCRIPT: tuple[PackEmission, ...] = (
    GoalDraft(value={"title": "요금제 반복 문의"}),
    PlanDraft(value={"steps": ["step-1"]}),
    ActivityDraft(payload={"activity": "step", "phase": "started", "step_id": "step-1"}),
    FactDraft(value={"metric": 6}, step_id="step-1"),
    NoteDraft(value={"text": "검증된 노트"}),
    ActivityDraft(payload={"activity": "step", "phase": "completed", "step_id": "step-1"}),
    ReportDraft(value={"headline": "결론"}, meta={"agent_mode": "fixture"}),
    OutcomeDraft(status="completed"),
)


class ScriptedPack:
    Input = DemoInput

    def __init__(
        self,
        emissions: Sequence[PackEmission | Exception],
        *,
        pack_id: str = "demo_pack",
        resume_emissions: Sequence[PackEmission | Exception] = (),
    ) -> None:
        self.spec = demo_spec(pack_id)
        self._emissions = list(emissions)
        self._resume_emissions = list(resume_emissions)
        self.contexts: list[PackContext] = []

    async def execute(
        self,
        request: DemoInput,
        context: PackContext,
    ) -> AsyncIterator[PackEmission]:
        self.contexts.append(context)
        script = self._resume_emissions if context.resumed else self._emissions
        for item in script:
            if isinstance(item, Exception):
                raise item
            yield item


async def run_pack(pack: ScriptedPack, kernel: PackKernel, run_id=None):
    committed: list[CanonicalRunEvent] = []

    async def sink(events):
        committed.extend(events)

    result = await kernel.run(
        pack,
        {"question": "무엇이 문제인가"},
        run_id=run_id or uuid4(),
        on_committed=sink,
    )
    return result, committed


async def test_happy_path_commits_ordered_canonical_events() -> None:
    journal = InMemoryEventJournal()
    kernel = PackKernel(journal)
    pack = ScriptedPack(HAPPY_SCRIPT)
    result, committed = await run_pack(pack, kernel)

    assert result.status == "completed"
    kinds = [event.kind for event in committed]
    assert kinds == [
        "run.opened",
        "artifact.committed",
        "artifact.committed",
        "activity.changed",
        "artifact.committed",
        "artifact.committed",
        "activity.changed",
        "artifact.committed",
        "run.completed",
    ]
    assert [event.sequence for event in committed] == list(range(1, len(committed) + 1))
    stored = [event async for event in journal.read(result.run_id)]
    assert stored == committed
    artifact_kinds = [
        event.payload["artifact_kind"]
        for event in committed
        if event.kind == "artifact.committed"
    ]
    assert artifact_kinds == ["goal", "plan", "fact", "note", "report"]
    report_event = committed[-2]
    assert report_event.payload["agent_mode"] == "fixture"
    assert report_event.artifact is not None
    assert report_event.artifact.schema_id == "demo_pack.report.v1"
    assert report_event.artifact.value == {"headline": "결론"}


async def test_plan_before_goal_fails_with_contract_violation() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack([PlanDraft(value={"steps": ["step-1"]})])
    result, committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "analysis_pack_contract_violation"
    assert committed[-1].kind == "run.failed"


async def test_missing_outcome_is_a_contract_violation() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack([GoalDraft(value={"title": "목표"})])
    result, committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error.code == "analysis_pack_contract_violation"
    assert committed[-1].kind == "run.failed"


async def test_completed_outcome_requires_a_report() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack(
        [GoalDraft(value={"title": "목표"}), OutcomeDraft(status="completed")]
    )
    result, _committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error.code == "analysis_pack_contract_violation"


async def test_invalid_artifact_value_fails_safely() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack([GoalDraft(value={"title": 3})])
    result, committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error.code == "analysis_pack_contract_violation"
    assert committed[-1].kind == "run.failed"


async def test_pack_degraded_maps_to_run_degraded() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    limitation = "요청한 기간에 데이터가 없습니다."
    pack = ScriptedPack(
        [GoalDraft(value={"title": "목표"}), PackDegraded((limitation,))]
    )
    result, committed = await run_pack(pack, kernel)
    assert result.status == "degraded"
    assert result.limitations == [limitation]
    assert committed[-1].kind == "run.degraded"
    assert committed[-1].payload["limitations"] == [limitation]


async def test_pack_degraded_without_limitations_still_terminates_safely() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack([GoalDraft(value={"title": "목표"}), PackDegraded(())])
    result, committed = await run_pack(pack, kernel)
    assert result.status == "degraded"
    assert result.limitations
    assert committed[-1].kind == "run.degraded"


async def test_pack_domain_error_keeps_declared_public_code() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack(
        [PackDomainError("unsupported_analysis", "지원하지 않는 요청입니다.")]
    )
    result, committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error.code == "unsupported_analysis"
    assert committed[-1].kind == "run.failed"
    assert committed[-1].payload["error"]["code"] == "unsupported_analysis"


async def test_unexpected_exception_is_sanitized() -> None:
    kernel = PackKernel(InMemoryEventJournal())
    pack = ScriptedPack([RuntimeError("secret internals leaked?")])
    result, committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error.code == "analysis_pack_failed"
    assert "secret" not in str(committed[-1].payload)


async def test_timeout_maps_to_analysis_timeout() -> None:
    class SlowPack(ScriptedPack):
        async def execute(self, request, context):
            self.contexts.append(context)
            await asyncio.sleep(5)
            yield GoalDraft(value={"title": "늦은 목표"})

    kernel = PackKernel(InMemoryEventJournal(), timeout_seconds=0.05)
    pack = SlowPack([])
    result, committed = await run_pack(pack, kernel)
    assert result.status == "failed"
    assert result.error.code == "analysis_timeout"
    assert committed[-1].kind == "run.failed"


async def test_awaiting_input_then_resume_continues_the_same_run() -> None:
    journal = InMemoryEventJournal()
    kernel = PackKernel(journal)
    run_id = uuid4()
    pack = ScriptedPack(
        [
            InteractionDraft(
                phase="requested",
                payload={"clarification_id": "clar-1", "question": "기간을 알려주세요."},
            ),
            OutcomeDraft(status="awaiting_input"),
        ],
        resume_emissions=HAPPY_SCRIPT,
    )
    first = await kernel.run(pack, {"question": "모호한 질문"}, run_id=run_id)
    assert first.status == "awaiting_input"
    events = [event async for event in journal.read(run_id)]
    assert [event.kind for event in events] == [
        "run.opened",
        "interaction.changed",
        "run.awaiting_input",
    ]

    resumed = await kernel.run(
        pack,
        {"question": "2026-07 기간"},
        run_id=run_id,
        resume_payload={"answer": "2026-07 기간"},
    )
    assert resumed.status == "completed"
    events = [event async for event in journal.read(run_id)]
    kinds = [event.kind for event in events]
    assert kinds[3:5] == ["interaction.changed", "run.resumed"]
    assert kinds[-1] == "run.completed"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert pack.contexts[0].resumed is False
    assert pack.contexts[1].resumed is True


async def test_invalid_input_creates_no_run() -> None:
    journal = InMemoryEventJournal()
    kernel = PackKernel(journal)
    pack = ScriptedPack(HAPPY_SCRIPT)
    run_id = uuid4()
    with pytest.raises(PackInputError):
        await kernel.run(pack, {"question": 12}, run_id=run_id)
    with pytest.raises(UnknownRunError):
        await journal.last_sequence(run_id)


def test_registry_rejects_duplicate_pack_ids() -> None:
    with pytest.raises(PackRegistrationError):
        AnalysisPackRegistry([ScriptedPack(HAPPY_SCRIPT), ScriptedPack(HAPPY_SCRIPT)])


def test_registry_rejects_the_same_instance_twice() -> None:
    pack = ScriptedPack(HAPPY_SCRIPT)
    with pytest.raises(PackRegistrationError):
        AnalysisPackRegistry([pack, pack])


def test_registry_rejects_schema_digest_conflicts() -> None:
    first = ScriptedPack(HAPPY_SCRIPT, pack_id="first_pack")
    second = ScriptedPack(HAPPY_SCRIPT, pack_id="second_pack")
    conflicting = ArtifactSchema(
        kind="goal", schema_id="first_pack.goal.v1", model=DemoPlan
    )
    second.spec = second.spec.model_copy(
        update={"artifact_schemas": (conflicting, *second.spec.artifact_schemas[1:])}
    )
    with pytest.raises(PackRegistrationError):
        AnalysisPackRegistry([first, second])


def test_registry_rejects_non_model_input() -> None:
    pack = ScriptedPack(HAPPY_SCRIPT)
    pack.Input = dict  # type: ignore[assignment]
    with pytest.raises(PackRegistrationError):
        AnalysisPackRegistry([pack])


def test_registry_resolves_registered_packs() -> None:
    pack = ScriptedPack(HAPPY_SCRIPT)
    registry = AnalysisPackRegistry([pack])
    assert registry.get("demo_pack") is pack
    assert registry.pack_ids() == ("demo_pack",)
    assert "demo_pack" in registry
    assert len(registry) == 1
