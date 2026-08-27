"""SourceOverviewPack: the second Analysis Pack, proving the seam.

A deliberately small, deterministic analysis that profiles the requested data
sources from their public manifests.  It shares no schema with the customer
signal pack: only the Canonical Run Event envelope and the trusted Catalog.
Registering it is one registry line; the Frontend contract does not change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from customer_signal.domain.sources import PublicSourceManifest, SourceManifest
from customer_signal.domain.types import SourceId
from customer_signal.packs.contracts import (
    ActivityDraft,
    AnalysisPackSpec,
    ArtifactSchema,
    FactDraft,
    GoalDraft,
    OutcomeDraft,
    PackContext,
    PackDomainError,
    PackEmission,
    PlanDraft,
    ReportDraft,
)


class _ManifestProvider(Protocol):
    def manifests(self, source_ids: Sequence[str]) -> list[SourceManifest]: ...


class SourceOverviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceOverviewInput(SourceOverviewContract):
    question: str = Field(min_length=1, max_length=500)
    enabled_sources: list[SourceId] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_unique_sources(self) -> Self:
        if len(self.enabled_sources) != len(set(self.enabled_sources)):
            raise ValueError("enabled_sources must be unique")
        return self


class SourceOverviewGoal(SourceOverviewContract):
    goal_id: str
    objective: str
    source_ids: list[SourceId]


class SourceOverviewPlan(SourceOverviewContract):
    plan_id: str
    steps: list[str] = Field(min_length=1, max_length=8)


class SourceProfile(SourceOverviewContract):
    source_id: SourceId
    label: str
    event_type_count: int = Field(ge=0)
    capability_count: int = Field(ge=0)
    capabilities: list[str]


class SourceOverviewFact(SourceOverviewContract):
    fact_id: str
    profiles: list[SourceProfile]


class SourceOverviewReport(SourceOverviewContract):
    headline: str
    source_count: int = Field(ge=0)
    highlights: list[str] = Field(max_length=32)


SOURCE_OVERVIEW_PACK_SPEC = AnalysisPackSpec(
    pack_id="source_overview",
    pack_version="1.0.0",
    title_ko="Source 개요 분석",
    description_ko="요청한 Source의 공개 manifest를 프로파일링하는 결정론적 분석입니다.",
    input_schema_id="source_overview.input.v1",
    artifact_schemas=(
        ArtifactSchema(
            kind="goal", schema_id="source_overview.goal.v1", model=SourceOverviewGoal
        ),
        ArtifactSchema(
            kind="plan", schema_id="source_overview.plan.v1", model=SourceOverviewPlan
        ),
        ArtifactSchema(
            kind="fact", schema_id="source_overview.fact.v1", model=SourceOverviewFact
        ),
        ArtifactSchema(
            kind="report",
            schema_id="source_overview.report.v1",
            model=SourceOverviewReport,
        ),
    ),
    required_catalog_keys=("Card", "Table", "Notice"),
)


class SourceOverviewPack:
    """Deterministic manifest profiling behind the same Pack seam."""

    Input = SourceOverviewInput
    spec = SOURCE_OVERVIEW_PACK_SPEC

    def __init__(self, registry: _ManifestProvider) -> None:
        self._registry = registry

    async def execute(
        self,
        request: SourceOverviewInput,
        context: PackContext,
    ) -> AsyncIterator[PackEmission]:
        try:
            manifests = self._registry.manifests(request.enabled_sources)
        except LookupError as error:
            raise PackDomainError(
                "unknown_source",
                "요청한 Source를 찾을 수 없습니다.",
            ) from error

        yield GoalDraft(
            value=SourceOverviewGoal(
                goal_id="goal-source-overview",
                objective=request.question,
                source_ids=list(request.enabled_sources),
            ).model_dump(mode="json")
        )
        yield PlanDraft(
            value=SourceOverviewPlan(
                plan_id="plan-source-overview",
                steps=["공개 manifest를 수집합니다.", "Source 프로파일을 요약합니다."],
            ).model_dump(mode="json")
        )
        yield ActivityDraft(
            payload={"activity": "step", "phase": "started", "step_id": "step-profile"}
        )

        profiles = []
        for manifest in manifests:
            public = PublicSourceManifest.from_internal(manifest)
            profiles.append(
                SourceProfile(
                    source_id=public.source_id,
                    label=public.label,
                    event_type_count=len(public.supported_event_types),
                    capability_count=len(public.capabilities),
                    capabilities=sorted(public.capabilities),
                )
            )
        profiles.sort(key=lambda profile: profile.source_id)
        yield FactDraft(
            value=SourceOverviewFact(
                fact_id="fact-source-overview",
                profiles=profiles,
            ).model_dump(mode="json"),
            step_id="step-profile",
        )
        yield ActivityDraft(
            payload={"activity": "step", "phase": "completed", "step_id": "step-profile"}
        )
        yield ReportDraft(
            value=SourceOverviewReport(
                headline=f"{len(profiles)}개 Source의 공개 프로파일을 확인했습니다.",
                source_count=len(profiles),
                highlights=[
                    f"{profile.source_id}: capability {profile.capability_count}개"
                    for profile in profiles
                ],
            ).model_dump(mode="json"),
            meta={"agent_mode": "fixture"},
        )
        yield OutcomeDraft(status="completed")


__all__ = [
    "SOURCE_OVERVIEW_PACK_SPEC",
    "SourceOverviewInput",
    "SourceOverviewPack",
]
