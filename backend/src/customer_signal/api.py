"""FastAPI application factory for analysis runs and the mounted MCP surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from pydantic import BaseModel, ConfigDict

from customer_signal.agent.contracts import RunRequest
from customer_signal.agent.fixture import FixtureRunner
from customer_signal.agent.gemini import GeminiRunner
from customer_signal.analytics.models import CustomerJourneyResult, EvidenceResult
from customer_signal.analytics.service import AnalyticsService
from customer_signal.config import Settings
from customer_signal.data.database import seed_database
from customer_signal.data.repository import DuckDBRepository
from customer_signal.mcp_server import create_mcp_server
from customer_signal.runtime.coordinator import (
    RunCoordinator,
    RunNotCompletedError,
    RunResourceNotFoundError,
)
from customer_signal.runtime.run_store import (
    InvalidLastEventIdError,
    RunSnapshot,
    RunStore,
    UnknownRunError,
)
from customer_signal.synthetic.generator import generate_dataset


class RunAccepted(BaseModel):
    """Links returned immediately after a run is queued."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str
    status_url: str
    events_url: str


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    """Injectable application services for deterministic tests and later runners."""

    store: RunStore
    coordinator: RunCoordinator
    mcp_server: FastMCP


def _default_dependencies(settings: Settings) -> ApiDependencies:
    if not settings.database_path.is_file():
        seed_database(settings.database_path, generate_dataset())
    repository = DuckDBRepository(settings.database_path)
    analytics = AnalyticsService(repository)
    mcp_server = create_mcp_server(analytics)
    store = RunStore()
    api_key = (
        settings.gemini_api_key.get_secret_value() if settings.gemini_api_key is not None else None
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
    )
    return ApiDependencies(store=store, coordinator=coordinator, mcp_server=mcp_server)


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

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(request: RunRequest) -> RunAccepted:
        snapshot = resolved.coordinator.create_run(request)
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

    app.mount("/mcp", mcp_http_app)
    return app


__all__ = ["ApiDependencies", "RunAccepted", "create_app"]
