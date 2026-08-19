"""Background run orchestration and run-scoped detail authorization."""

from __future__ import annotations

import asyncio
from typing import cast

from customer_signal.agent.contracts import (
    AnalysisRunner,
    RunRequest,
    UnsupportedClaimError,
    UnsupportedQuestionError,
)
from customer_signal.analytics.models import CustomerJourneyResult, EvidenceResult
from customer_signal.analytics.service import AnalyticsService
from customer_signal.data.repository import EntityNotFoundError
from customer_signal.runtime.events import RunnerEvent
from customer_signal.runtime.run_store import RunError, RunSnapshot, RunStore


class RunResourceNotFoundError(LookupError):
    """Raised for every absent or unauthorized run-scoped detail."""


class RunNotCompletedError(RuntimeError):
    """Raised when detail access is attempted before successful completion."""


def _error_from_payload(payload: dict[str, object]) -> RunError:
    code = payload.get("code")
    if code == "unsupported_question":
        return RunError(
            code=code,
            message="검색 실패와 고객 문의 Journey 질문만 지원합니다.",
        )
    if code == "unsupported_claim":
        return RunError(code=code, message="분석 결과 검증에 실패했습니다.")
    if code == "tool_execution_failed":
        return RunError(code=code, message="분석 Tool 실행에 실패했습니다.")
    return RunError(code="run_failed", message="분석 실행에 실패했습니다.")


def _error_from_exception(error: Exception) -> RunError:
    if isinstance(error, UnsupportedQuestionError):
        return RunError(code=error.code, message=str(error))
    if isinstance(error, UnsupportedClaimError):
        return RunError(code=error.code, message="분석 결과 검증에 실패했습니다.")
    return RunError(code="run_failed", message="분석 실행에 실패했습니다.")


class RunCoordinator:
    """Execute runners and guard all post-run analytics by their private facts."""

    def __init__(
        self,
        *,
        runner: AnalysisRunner,
        analytics: AnalyticsService,
        store: RunStore,
    ) -> None:
        self._runner = runner
        self._analytics = analytics
        self._store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._journey_cache: dict[tuple[str, str], CustomerJourneyResult] = {}
        self._journey_evidence_ids: dict[str, set[str]] = {}
        self._closing = False

    def create_run(self, request: RunRequest) -> RunSnapshot:
        if self._closing:
            raise RuntimeError("run coordinator is closing")
        snapshot = self._store.create_run(request)
        self._tasks[snapshot.run_id] = asyncio.create_task(
            self._execute(snapshot.run_id, request.model_copy(deep=True)),
            name=f"analysis-run-{snapshot.run_id}",
        )
        return snapshot

    async def wait_for_run(self, run_id: str) -> RunSnapshot:
        task = self._tasks.get(run_id)
        if task is None:
            return self._store.get_snapshot(run_id)
        await asyncio.shield(task)
        return self._store.get_snapshot(run_id)

    async def close(self) -> None:
        self._closing = True
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for run_id in self._tasks:
            snapshot = self._store.get_snapshot(run_id)
            if snapshot.status not in {"queued", "running"}:
                continue
            public_error = RunError(
                code="run_cancelled",
                message="분석 실행이 취소됐습니다.",
            )
            await self._store.append_event(
                run_id,
                "error",
                public_error.model_dump(mode="json"),
            )
            await self._store.mark_failed(run_id, public_error)
            await self._store.append_event(run_id, "done", {"status": "failed"})

    def get_journey(self, run_id: str, customer_id: str) -> CustomerJourneyResult:
        snapshot, outcome = self._completed_outcome(run_id)
        if customer_id not in outcome.facts.allowed_customer_ids:
            raise RunResourceNotFoundError("run resource not found")

        cache_key = (run_id, customer_id)
        cached = self._journey_cache.get(cache_key)
        if cached is None:
            try:
                cached = self._analytics.get_customer_journey(
                    customer_id,
                    snapshot.request.start_at,
                    snapshot.request.end_at,
                    snapshot.request.enabled_sources,
                )
            except (EntityNotFoundError, ValueError) as error:
                raise RunResourceNotFoundError("run resource not found") from error
            self._journey_cache[cache_key] = cached.model_copy(deep=True)
            self._journey_evidence_ids.setdefault(run_id, set()).update(cached.evidence_ids)
        return cached.model_copy(deep=True)

    def get_evidence(self, run_id: str, evidence_id: str) -> EvidenceResult:
        _snapshot, outcome = self._completed_outcome(run_id)
        journey_evidence = self._journey_evidence_ids.get(run_id, set())
        if (
            evidence_id not in outcome.facts.allowed_evidence_ids
            and evidence_id not in journey_evidence
        ):
            raise RunResourceNotFoundError("run resource not found")
        try:
            return self._analytics.get_evidence([evidence_id])
        except (EntityNotFoundError, ValueError) as error:
            raise RunResourceNotFoundError("run resource not found") from error

    async def _execute(self, run_id: str, request: RunRequest) -> None:
        await self._store.mark_running(run_id)
        reported_error: RunError | None = None
        pending_result: RunnerEvent | None = None

        async def emit(event: RunnerEvent) -> None:
            nonlocal pending_result, reported_error
            if reported_error is not None:
                return
            if event.type == "error":
                reported_error = _error_from_payload(cast(dict[str, object], event.payload))
                await self._store.append_event(
                    run_id,
                    "error",
                    reported_error.model_dump(mode="json"),
                )
                return
            if event.type == "result":
                pending_result = event
                return
            await self._store.append_event(run_id, event.type, event.payload)

        async def fail_run(public_error: RunError) -> None:
            if reported_error is None:
                await self._store.append_event(
                    run_id,
                    "error",
                    public_error.model_dump(mode="json"),
                )
            await self._store.mark_failed(run_id, public_error)
            await self._store.append_event(run_id, "done", {"status": "failed"})

        try:
            outcome = await self._runner.run(request, emit=emit)
        except asyncio.CancelledError:
            public_error = reported_error or RunError(
                code="run_cancelled",
                message="분석 실행이 취소됐습니다.",
            )
            await fail_run(public_error)
            raise
        except Exception as error:
            public_error = reported_error or _error_from_exception(error)
            await fail_run(public_error)
        else:
            if reported_error is not None:
                await fail_run(reported_error)
                return
            if pending_result is not None:
                await self._store.append_event(
                    run_id,
                    pending_result.type,
                    pending_result.payload,
                )
            await self._store.mark_completed(run_id, outcome)
            await self._store.append_event(run_id, "done", {"status": "completed"})

    def _completed_outcome(self, run_id: str):
        snapshot = self._store.get_snapshot(run_id)
        if snapshot.status != "completed":
            raise RunNotCompletedError("run has not completed successfully")
        outcome = self._store.get_outcome(run_id)
        if outcome is None:
            raise RunNotCompletedError("run has no completed outcome")
        return snapshot, outcome


__all__ = [
    "RunCoordinator",
    "RunNotCompletedError",
    "RunResourceNotFoundError",
]
