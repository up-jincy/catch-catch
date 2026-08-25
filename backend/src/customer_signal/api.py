"""FastAPI application factory for analysis runs and the mounted MCP surface."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from pydantic import BaseModel, ConfigDict, Field

from customer_signal.agent.analysis_loop import AnalysisLoop
from customer_signal.agent.contracts import RunRequest
from customer_signal.agent.fixture import FixtureRunner
from customer_signal.agent.gemini import GeminiRunner
from customer_signal.agent.generic_fixture import GenericFixtureModel
from customer_signal.agent.generic_gemini import GeminiAnalysisModel
from customer_signal.analytics.executor import PrimitiveExecutor
from customer_signal.analytics.models import CustomerJourneyResult, EvidenceResult
from customer_signal.analytics.service import AnalyticsService
from customer_signal.config import Settings
from customer_signal.data.database import (
    SYNTHETIC_DATASET_VERSION,
    is_database_ready,
    seed_database,
)
from customer_signal.data.repository import DuckDBRepository
from customer_signal.data.source_registry import SourceRegistry
from customer_signal.domain.models import EvidenceRecord
from customer_signal.domain.sources import PublicSourceList, PublicSourceManifest
from customer_signal.mcp_server import create_mcp_server
from customer_signal.runtime.coordinator import (
    RunCoordinator,
    RunNotCompletedError,
    RunResourceNotFoundError,
)
from customer_signal.runtime.artifact_store import (
    ArtifactNotFoundError,
    ArtifactStore,
    InvalidRunIdError,
)
from customer_signal.runtime.artifacts import ArtifactListResponse
from customer_signal.runtime.document_renderer import render_document, render_markdown_bytes
from customer_signal.runtime.run_store import (
    InvalidLastEventIdError,
    InvalidRunTransitionError,
    RequestedAgentMode,
    RunSnapshot,
    RunStore,
    UnknownRunError,
)
from customer_signal.synthetic.adapter import SyntheticDuckDBAdapter
from customer_signal.synthetic.generator import generate_dataset
from customer_signal.synthetic.manifest import synthetic_source_manifest


class RunAccepted(BaseModel):
    """Links returned immediately after a run is queued."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str
    status_url: str
    events_url: str


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str = Field(min_length=1, max_length=1_000)


_BUILT_IN_SOURCE_IDS = (
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
)
@dataclass(frozen=True, slots=True)
class ApiDependencies:
    """Injectable application services for deterministic tests and later runners."""

    store: RunStore
    coordinator: RunCoordinator
    mcp_server: FastMCP
    registry: SourceRegistry | None = None
    artifact_store: ArtifactStore | None = None
    source_ids: tuple[str, ...] = ()
    generic_default_mode: RequestedAgentMode = "fixture"


class _RepositoryEvidenceProvider:
    def __init__(self, repository: DuckDBRepository) -> None:
        self._repository = repository

    def get_evidence(
        self,
        allowed_evidence_ids: Sequence[str],
    ) -> list[EvidenceRecord]:
        return [
            record.model_copy(update={"raw_fields": {}})
            for record in self._repository.get_evidence(allowed_evidence_ids)
        ]


class _FunctionalFixtureModel(GenericFixtureModel):
    """Bind the three deterministic demo Plans to their authoritative sources."""

    async def create_plan(self, goal, manifests):
        plan = await super().create_plan(goal, manifests)
        scenario = goal.goal_id.removeprefix("goal-")
        scopes = {
            "negative": {
                "step-profile": ["search_feedback"],
                "step-negative-topic": ["search_feedback"],
            },
            "repeat": {
                "step-repeat-sequence": ["search_history", "voc"],
                "step-repeat-journey": ["search_history", "voc"],
                "step-repeat-evidence": ["search_history", "voc"],
            },
            "signup": {
                "step-signup-sequence": ["subscription"],
                "step-signup-segment": ["subscription"],
            },
        }.get(scenario, {})
        steps = []
        for step in plan.steps:
            parameters = step.parameters
            configured_sources = scopes.get(step.step_id)
            source_ids = step.source_ids
            if configured_sources is not None:
                source_ids = [
                    source_id
                    for source_id in configured_sources
                    if source_id in goal.source_ids
                ] or step.source_ids
            if step.step_id == "step-negative-topic":
                parameters = parameters.model_copy(
                    update={
                        "predicates": [
                            "outcome == 'negative'",
                            "topic == '요금제 변경'",
                        ]
                    }
                )
            steps.append(
                step.model_copy(
                    update={
                        "source_ids": source_ids,
                        "parameters": parameters,
                    }
                )
            )
        return plan.model_copy(update={"steps": steps})


def _default_dependencies(settings: Settings) -> ApiDependencies:
    dataset = generate_dataset()
    if not is_database_ready(settings.database_path):
        seed_database(settings.database_path, dataset)
    repository = DuckDBRepository(settings.database_path)
    analytics = AnalyticsService(repository)
    mcp_server = create_mcp_server(analytics)
    manifests = [
        synthetic_source_manifest(source_id, dataset.events) for source_id in _BUILT_IN_SOURCE_IDS
    ]
    adapters = [SyntheticDuckDBAdapter(repository, manifest) for manifest in manifests]
    registry = SourceRegistry(
        adapters,
        evidence=_RepositoryEvidenceProvider(repository),
    )
    executor = PrimitiveExecutor(
        registry=registry,
        dataset_version=str(SYNTHETIC_DATASET_VERSION),
    )
    artifact_store = ArtifactStore.from_settings(settings)
    store = RunStore(artifact_store.list_artifacts())
    api_key = (
        settings.gemini_api_key.get_secret_value() if settings.gemini_api_key is not None else None
    )
    functional_model = _FunctionalFixtureModel()
    generic_fixture_loop = AnalysisLoop(
        model=functional_model,
        executor=executor,
        registry=registry,
    )
    generic_gemini_loop = None
    if api_key and api_key.strip():
        generic_gemini_loop = AnalysisLoop(
            model=GeminiAnalysisModel(
                api_key=api_key,
                primary_model=settings.gemini_model,
                fallback_model=settings.gemini_fallback_model,
            ),
            executor=executor,
            registry=registry,
        )
    coordinator = RunCoordinator(
        agent_mode=settings.agent_mode,
        fixture_runner=FixtureRunner(mcp_server),
        gemini_runner=GeminiRunner(
            api_key=api_key,
            mcp_url=f"http://{settings.api_host}:{settings.api_port}/mcp/",
            primary_model=settings.gemini_model,
            fallback_model=settings.gemini_fallback_model,
        ),
        analytics=analytics,
        store=store,
        generic_fixture_loop=generic_fixture_loop,
        generic_gemini_loop=generic_gemini_loop,
        artifact_store=artifact_store,
    )
    return ApiDependencies(
        store=store,
        coordinator=coordinator,
        mcp_server=mcp_server,
        registry=registry,
        artifact_store=artifact_store,
        source_ids=_BUILT_IN_SOURCE_IDS,
        generic_default_mode=settings.resolved_agent_mode,
    )


def create_app(
    settings: Settings | None = None,
    dependencies: ApiDependencies | None = None,
) -> FastAPI:
    """Build the complete ASGI app without import-time database writes."""

    resolved_settings = settings or Settings()
    resolved = dependencies or _default_dependencies(resolved_settings)
    mcp_http_app = resolved.mcp_server.http_app(path="/")

    @asynccontextmanager
    async def api_lifespan(_app: FastAPI):
        try:
            yield {}
        finally:
            await resolved.coordinator.close()

    app = FastAPI(lifespan=combine_lifespans(mcp_http_app.lifespan, api_lifespan))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[resolved_settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def snapshot_or_404(run_id: str) -> RunSnapshot:
        try:
            return resolved.store.get_snapshot(run_id)
        except UnknownRunError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error

    def artifact_or_404(run_id: str):
        if resolved.artifact_store is None:
            raise HTTPException(status_code=404, detail="Run Artifact not found")
        try:
            return resolved.artifact_store.load(run_id)
        except (InvalidRunIdError, ArtifactNotFoundError) as error:
            raise HTTPException(status_code=404, detail="Run Artifact not found") from error

    def event_cursor(
        run_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> int:
        if last_event_id is None:
            cursor = 0
        else:
            try:
                cursor = int(last_event_id)
            except ValueError as error:
                raise HTTPException(
                    status_code=400,
                    detail="Last-Event-ID must be a non-negative integer",
                ) from error
        try:
            resolved.store.validate_last_event_id(run_id, cursor)
        except UnknownRunError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error
        except InvalidLastEventIdError as error:
            raise HTTPException(
                status_code=400,
                detail="Last-Event-ID must name an emitted event or zero",
            ) from error
        return cursor

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sources")
    async def list_sources() -> PublicSourceList:
        if resolved.registry is None:
            return PublicSourceList(items=[])
        manifests = resolved.registry.manifests(resolved.source_ids)
        return PublicSourceList(
            items=[PublicSourceManifest.from_internal(item) for item in manifests]
        )

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(
        request: RunRequest,
        mode: RequestedAgentMode | None = Query(default=None),
    ) -> RunAccepted:
        if resolved.registry is not None:
            try:
                resolved.registry.manifests(request.enabled_sources)
            except LookupError as error:
                raise HTTPException(
                    status_code=422,
                    detail="enabled_sources contains an unknown source",
                ) from error
        selected_mode = mode or resolved.generic_default_mode
        snapshot = resolved.coordinator.create_run(
            request,
            generic=True,
            mode=selected_mode,
        )
        status_url = f"/api/runs/{snapshot.run_id}"
        return RunAccepted(
            run_id=snapshot.run_id,
            status_url=status_url,
            events_url=f"{status_url}/events",
        )

    @app.post(
        "/api/runs/{run_id}/clarification",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def answer_clarification(
        run_id: str,
        answer: ClarificationAnswer,
    ) -> RunAccepted:
        try:
            snapshot = await resolved.coordinator.answer_clarification(
                run_id,
                answer.answer,
            )
        except UnknownRunError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error
        except (InvalidRunTransitionError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail="Run is not awaiting clarification",
            ) from error
        status_url = f"/api/runs/{snapshot.run_id}"
        return RunAccepted(
            run_id=snapshot.run_id,
            status_url=status_url,
            events_url=f"{status_url}/events",
        )

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> RunSnapshot:
        return snapshot_or_404(run_id)

    @app.get("/api/runs/{run_id}/events", response_class=EventSourceResponse)
    async def stream_run_events(
        run_id: str,
        cursor: int = Depends(event_cursor),
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in resolved.store.stream_events(run_id, cursor):
            yield ServerSentEvent(
                id=str(event.id),
                event=event.type,
                data={
                    "run_id": run_id,
                    "type": event.type,
                    "payload": event.payload,
                },
            )

    @app.get("/api/runs/{run_id}/customers/{customer_id}/journey")
    async def get_journey(run_id: str, customer_id: str) -> CustomerJourneyResult:
        try:
            return resolved.coordinator.get_journey(run_id, customer_id)
        except UnknownRunError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error
        except RunResourceNotFoundError as error:
            raise HTTPException(status_code=404, detail="Run resource not found") from error
        except RunNotCompletedError as error:
            raise HTTPException(status_code=409, detail="Run is not completed") from error

    @app.get("/api/runs/{run_id}/evidence/{evidence_id}")
    async def get_evidence(run_id: str, evidence_id: str) -> EvidenceResult:
        try:
            return resolved.coordinator.get_evidence(run_id, evidence_id)
        except UnknownRunError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error
        except RunResourceNotFoundError as error:
            raise HTTPException(status_code=404, detail="Run resource not found") from error
        except RunNotCompletedError as error:
            raise HTTPException(status_code=409, detail="Run is not completed") from error

    @app.get("/api/run-artifacts", response_model=ArtifactListResponse)
    async def list_run_artifacts() -> ArtifactListResponse:
        if resolved.artifact_store is None:
            return ArtifactListResponse(artifacts=[])
        return ArtifactListResponse(artifacts=resolved.artifact_store.list_summaries())

    @app.get("/api/run-artifacts/{run_id}")
    async def get_run_artifact(run_id: str):
        return artifact_or_404(run_id)

    @app.get("/api/run-artifacts/{run_id}/document")
    async def get_run_document(run_id: str):
        return render_document(artifact_or_404(run_id))

    @app.get("/api/run-artifacts/{run_id}/download.json")
    async def download_run_json(run_id: str) -> Response:
        artifact = artifact_or_404(run_id)
        return Response(
            content=resolved.artifact_store.load_bytes(run_id),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{artifact.run_id}.json"'},
        )

    @app.get("/api/run-artifacts/{run_id}/download.md")
    async def download_run_markdown(run_id: str) -> Response:
        artifact = artifact_or_404(run_id)
        return Response(
            content=render_markdown_bytes(artifact),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{artifact.run_id}.md"'},
        )

    app.mount("/mcp", mcp_http_app)
    return app

__all__ = ["ApiDependencies", "RunAccepted", "create_app"]
