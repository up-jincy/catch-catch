"""Background run orchestration and run-scoped detail authorization."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Literal, cast
from uuid import UUID

from customer_signal.agent.contracts import (
    AnalysisRunner,
    RunRequest,
    RunnerOutcome,
    UnsupportedClaimError,
    UnsupportedQuestionError,
)
from customer_signal.agent.gemini import GeminiRunnerError
from customer_signal.analytics.models import CustomerJourneyResult, EvidenceResult
from customer_signal.analytics.service import AnalyticsService
from customer_signal.data.repository import EntityNotFoundError
from customer_signal.domain.analysis import PublicRunError
from customer_signal.domain.facts import AnalysisFact
from customer_signal.observability.langfuse import (
    LangfuseRunContext,
    bind_langfuse_run,
    flush_langfuse,
    update_langfuse_workflow,
)
from customer_signal.journal.events import CanonicalRunEvent
from customer_signal.packs.customer_signal import CustomerSignalPack
from customer_signal.packs.kernel import PackKernel
from customer_signal.packs.registry import AnalysisPackRegistry
from customer_signal.runtime.artifact_store import ArtifactStore, ArtifactWriteError
from customer_signal.runtime.artifacts import RunArtifact, RunVersions
from customer_signal.runtime.events import RunnerEvent
from customer_signal.runtime.wire_projection import wire_events_for
from customer_signal.runtime.run_store import (
    InvalidRunTransitionError,
    RequestedAgentMode,
    RunError,
    RunSnapshot,
    RunStore,
)


class RunResourceNotFoundError(LookupError):
    """Raised for every absent or unauthorized run-scoped detail."""


class RunNotCompletedError(RuntimeError):
    """Raised when detail access is attempted before successful completion."""


_GEMINI_ERROR_MESSAGES = {
    "unsupported_question": "검색 실패와 고객 문의 Journey 질문만 지원합니다.",
    "gemini_not_configured": "Gemini API Key가 설정되지 않았습니다.",
    "gemini_model_not_found": "사용 가능한 Gemini 분석 모델을 찾지 못했습니다.",
    "gemini_provider_failed": "Gemini 분석 서비스 호출에 실패했습니다.",
    "gemini_timeout": "Gemini 분석 시간이 초과됐습니다.",
    "gemini_validation_failed": "Gemini 분석 결과 검증에 실패했습니다.",
    "gemini_tool_policy_failed": "Gemini Tool 호출 정책 검증에 실패했습니다.",
    "gemini_tool_execution_failed": "Gemini MCP Tool 실행에 실패했습니다.",
}


def _safe_gemini_error(error: GeminiRunnerError) -> RunError:
    code = error.code if error.code in _GEMINI_ERROR_MESSAGES else "gemini_provider_failed"
    return RunError(code=code, message=_GEMINI_ERROR_MESSAGES[code])


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
    if isinstance(error, GeminiRunnerError):
        return _safe_gemini_error(error)
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
        runner: AnalysisRunner | None = None,
        agent_mode: Literal["auto", "fixture", "gemini"] = "fixture",
        fixture_runner: AnalysisRunner | None = None,
        gemini_runner: AnalysisRunner | None = None,
        gemini_timeout_seconds: float = 45.0,
        analytics: AnalyticsService,
        store: RunStore,
        kernel: PackKernel | None = None,
        packs: AnalysisPackRegistry | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        if gemini_timeout_seconds <= 0:
            raise ValueError("gemini_timeout_seconds must be positive")
        if runner is not None:
            if fixture_runner is not None or gemini_runner is not None:
                raise ValueError("runner cannot be combined with mode-specific runners")
            self._runner = runner
            self._agent_mode: Literal["fixed", "auto", "fixture", "gemini"] = "fixed"
        else:
            if agent_mode in {"auto", "fixture"} and fixture_runner is None:
                raise ValueError("fixture_runner is required for fixture and auto modes")
            if agent_mode == "gemini" and gemini_runner is None:
                raise ValueError("gemini_runner is required for gemini mode")
            self._runner = None
            self._agent_mode = agent_mode
        self._fixture_runner = fixture_runner
        self._gemini_runner = gemini_runner
        self._gemini_timeout_seconds = gemini_timeout_seconds
        self._analytics = analytics
        self._store = store
        self._kernel = kernel
        self._packs = packs
        self._artifact_store = artifact_store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._journey_cache: dict[tuple[str, str], CustomerJourneyResult] = {}
        self._journey_evidence_ids: dict[str, set[str]] = {}
        self._closing = False

    def create_run(
        self,
        request: RunRequest,
        *,
        generic: bool = False,
        mode: RequestedAgentMode | None = None,
    ) -> RunSnapshot:
        if self._closing:
            raise RuntimeError("run coordinator is closing")
        selected_mode = mode or cast(RequestedAgentMode, self._agent_mode)
        if selected_mode == "fixed":
            selected_mode = "fixture"
        snapshot = self._store.create_run(
            request,
            run_kind="generic" if generic else "legacy",
            requested_mode=selected_mode,
        )
        self._checkpoint(snapshot.run_id)
        execute = self._execute_generic if generic else self._execute
        self._tasks[snapshot.run_id] = asyncio.create_task(
            execute(snapshot.run_id, request.model_copy(deep=True)),
            name=f"analysis-run-{snapshot.run_id}",
        )
        return snapshot

    async def answer_clarification(self, run_id: str, answer: str) -> RunSnapshot:
        """Resume a generic Run in-place using the user's bounded answer as the question."""

        if self._closing:
            raise RuntimeError("run coordinator is closing")
        if self._store.get_run_kind(run_id) != "generic":
            raise InvalidRunTransitionError("legacy Runs do not accept clarification")
        snapshot = await self._store.answer_clarification(run_id, answer)
        self._checkpoint(run_id)
        resumed_request = snapshot.request.model_copy(update={"question": answer.strip()})
        self._tasks[run_id] = asyncio.create_task(
            self._execute_generic(run_id, resumed_request, already_running=True),
            name=f"analysis-run-resume-{run_id}",
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
            self._checkpoint(run_id)
        await asyncio.to_thread(flush_langfuse)

    def get_journey(self, run_id: str, customer_id: str) -> CustomerJourneyResult:
        snapshot = self._completed_snapshot(run_id)
        allowed_customers, allowed_evidence = self._authorization(run_id, snapshot)
        if customer_id not in allowed_customers:
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
            if snapshot.run_kind == "generic":
                events = [event for event in cached.events if event.evidence_id in allowed_evidence]
                cached = cached.model_copy(
                    update={
                        "events": events,
                        "evidence_ids": [event.evidence_id for event in events],
                        "stats": cached.stats.model_copy(update={"returned_rows": len(events)}),
                    }
                )
            self._journey_cache[cache_key] = cached.model_copy(deep=True)
            if snapshot.run_kind == "legacy":
                self._journey_evidence_ids.setdefault(run_id, set()).update(cached.evidence_ids)
        return cached.model_copy(deep=True)

    def get_evidence(self, run_id: str, evidence_id: str) -> EvidenceResult:
        snapshot = self._completed_snapshot(run_id)
        _allowed_customers, allowed_evidence = self._authorization(run_id, snapshot)
        journey_evidence = self._journey_evidence_ids.get(run_id, set())
        if evidence_id not in allowed_evidence and evidence_id not in journey_evidence:
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
            self._checkpoint(run_id)

        context = LangfuseRunContext(
            run_id=run_id,
            run_kind="legacy",
            question=request.question,
            source_ids=tuple(request.enabled_sources),
        )
        try:
            with bind_langfuse_run(context):
                outcome = await self._run_selected(request, emit=emit)
                update_langfuse_workflow(
                    output={
                        "status": outcome.status,
                        "agent_mode": outcome.agent_mode,
                        "outcome_kind": outcome.outcome_kind,
                    }
                )
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
            self._checkpoint(run_id)

    async def _execute_generic(
        self,
        run_id: str,
        request: RunRequest,
        *,
        already_running: bool = False,
    ) -> None:
        """Run the generic analysis through the Pack Kernel with the journal as truth."""

        if not already_running:
            await self._store.mark_running(run_id)

        published_terminal = False

        async def publish(events: Sequence[CanonicalRunEvent]) -> None:
            nonlocal published_terminal
            for event in events:
                for wire_type, payload in wire_events_for(event):
                    try:
                        await self._store.append_generic_event(run_id, wire_type, payload)
                    except Exception:
                        # A wire-projection failure must not fail the canonical Run.
                        continue
                    if wire_type == "done":
                        published_terminal = True
            self._checkpoint(run_id)

        context = LangfuseRunContext(
            run_id=run_id,
            run_kind="generic",
            question=request.question,
            source_ids=tuple(request.enabled_sources),
        )
        outcome = None
        try:
            kernel, pack = self._customer_signal_runtime()
            mode = self._store.get_requested_mode(run_id)
            with bind_langfuse_run(context):
                result = await kernel.run(
                    pack,
                    request,
                    run_id=UUID(run_id),
                    options={"mode": mode},
                    resume_payload=(
                        {"answer": request.question} if already_running else None
                    ),
                    on_committed=publish,
                )
                outcome = pack.take_outcome(UUID(run_id))
                update_langfuse_workflow(
                    output={
                        "status": result.status,
                        "agent_mode": outcome.agent_mode if outcome else None,
                        "outcome_kind": outcome.outcome_kind if outcome else None,
                        "fact_count": len(outcome.facts) if outcome else 0,
                        "note_count": len(outcome.notes) if outcome else 0,
                    }
                )
        except asyncio.CancelledError:
            public_error = PublicRunError(
                code="run_cancelled",
                message="분석 실행이 취소됐습니다.",
            )
            await self._finalize_generic_failure(run_id, public_error, published_terminal)
            raise
        except Exception:
            public_error = PublicRunError(
                code="generic_run_failed",
                message="분석 실행에 실패했습니다.",
            )
            await self._finalize_generic_failure(run_id, public_error, published_terminal)
            return

        if result.status == "awaiting_input":
            if outcome is not None and outcome.status == "awaiting_clarification":
                await self._store.mark_awaiting(run_id, outcome)
            self._checkpoint(run_id)
            return

        if outcome is not None and outcome.status == result.status:
            await self._store.mark_generic_terminal(run_id, outcome)
        else:
            failure = result.error or PublicRunError(
                code="generic_run_failed",
                message="분석 실행에 실패했습니다.",
            )
            try:
                await self._store.mark_failed(run_id, failure)
            except InvalidRunTransitionError:
                pass
        self._checkpoint(run_id)

    async def _finalize_generic_failure(
        self,
        run_id: str,
        error: PublicRunError,
        published_terminal: bool,
    ) -> None:
        if not published_terminal:
            try:
                await self._store.append_generic_event(
                    run_id,
                    "error",
                    error.model_dump(mode="json"),
                )
                await self._store.append_generic_event(
                    run_id,
                    "done",
                    {"status": "failed", "limitations": []},
                )
            except Exception:
                pass
        try:
            await self._store.mark_failed(run_id, error)
        except InvalidRunTransitionError:
            pass
        self._checkpoint(run_id)

    def _customer_signal_runtime(self) -> tuple[PackKernel, CustomerSignalPack]:
        if self._kernel is None or self._packs is None:
            raise RuntimeError("analysis pack runtime is not configured")
        pack = self._packs.get("customer_signal")
        if not isinstance(pack, CustomerSignalPack):
            raise RuntimeError("customer_signal pack is not registered")
        return self._kernel, pack

    def _checkpoint(self, run_id: str) -> None:
        if self._artifact_store is None:
            return
        snapshot = self._store.get_snapshot(run_id)
        outcome = self._store.get_outcome(run_id)
        outcome_model = getattr(outcome, "model", None)
        versions = _versions_from_facts(
            snapshot.facts,
            model_version=outcome_model or snapshot.agent_mode,
            generic=snapshot.run_kind == "generic",
        )
        public_error = snapshot.error
        if isinstance(public_error, RunError):
            public_error = PublicRunError(
                code=public_error.code,
                message=public_error.message,
            )
        artifact = RunArtifact(
            run_id=UUID(snapshot.run_id),
            status=snapshot.status,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
            completed_at=(
                snapshot.updated_at
                if snapshot.status in {"completed", "degraded", "failed"}
                else None
            ),
            request=snapshot.request,
            goal=snapshot.goal,
            clarification=snapshot.clarification,
            plan=snapshot.plan,
            plan_history=snapshot.plan_history,
            facts=snapshot.facts,
            notes=snapshot.notes,
            report=snapshot.report,
            last_event_id=snapshot.last_event_id,
            versions=versions,
            failed_step_id=snapshot.failed_step_id,
            limitations=snapshot.limitations,
            error=public_error,
        )
        try:
            self._artifact_store.save(artifact)
        except ArtifactWriteError:
            # Runtime delivery remains usable if local history persistence is unavailable.
            return

    async def _run_selected(
        self,
        request: RunRequest,
        *,
        emit,
    ) -> RunnerOutcome:
        if self._agent_mode == "fixed":
            assert self._runner is not None
            return await self._runner.run(request, emit=emit)
        if self._agent_mode == "fixture":
            assert self._fixture_runner is not None
            return await self._fixture_runner.run(request, emit=emit)
        if self._agent_mode == "gemini":
            return await self._run_gemini_with_timeout(request, emit=emit)

        fallback_code = "gemini_not_configured"
        if self._gemini_runner is not None:
            try:
                return await self._run_gemini_with_timeout(request, emit=emit)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if isinstance(error, GeminiRunnerError):
                    if error.code == "unsupported_question":
                        raise
                    fallback_code = _safe_gemini_error(error).code
                else:
                    fallback_code = "gemini_provider_failed"
        fallback_message = (
            "Gemini 분석 시간이 초과되어 fixture 모드로 전환했습니다."
            if fallback_code == "gemini_timeout"
            else "Gemini 분석을 사용할 수 없어 fixture 모드로 전환했습니다."
        )
        await emit(
            RunnerEvent(
                type="fallback",
                payload={
                    "from": "gemini",
                    "to": "fixture",
                    "code": fallback_code,
                    "message": fallback_message,
                },
            )
        )
        assert self._fixture_runner is not None
        return await self._fixture_runner.run(request, emit=emit)

    async def _run_gemini_with_timeout(self, request: RunRequest, *, emit) -> RunnerOutcome:
        try:
            async with asyncio.timeout(self._gemini_timeout_seconds):
                return await self._run_gemini(request, emit=emit)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise GeminiRunnerError(
                "gemini_timeout",
                _GEMINI_ERROR_MESSAGES["gemini_timeout"],
            ) from error

    async def _run_gemini(self, request: RunRequest, *, emit) -> RunnerOutcome:
        if self._gemini_runner is None:
            raise GeminiRunnerError(
                "gemini_not_configured",
                "Gemini 분석 모드가 설정되지 않았습니다.",
            )
        reported_error: RunnerEvent | None = None
        pending_result: RunnerEvent | None = None

        async def emit_without_error(event: RunnerEvent) -> None:
            nonlocal pending_result, reported_error
            if event.type == "error":
                reported_error = event
                return
            if event.type == "result":
                pending_result = event
                return
            await emit(event)

        outcome = await self._gemini_runner.run(request, emit=emit_without_error)
        if reported_error is not None:
            raise GeminiRunnerError(
                "gemini_provider_failed",
                "Gemini 분석 서비스 호출에 실패했습니다.",
            )
        if pending_result is not None:
            await emit(pending_result)
        return outcome

    def _completed_outcome(self, run_id: str):
        snapshot = self._store.get_snapshot(run_id)
        if snapshot.status != "completed":
            raise RunNotCompletedError("run has not completed successfully")
        outcome = self._store.get_outcome(run_id)
        if outcome is None:
            raise RunNotCompletedError("run has no completed outcome")
        return snapshot, outcome

    def _completed_snapshot(self, run_id: str) -> RunSnapshot:
        snapshot = self._store.get_snapshot(run_id)
        if snapshot.status != "completed":
            raise RunNotCompletedError("run has not completed successfully")
        return snapshot

    def _authorization(
        self,
        run_id: str,
        snapshot: RunSnapshot,
    ) -> tuple[set[str], set[str]]:
        if snapshot.run_kind == "generic":
            return (
                {customer_id for fact in snapshot.facts for customer_id in fact.customer_ids},
                {evidence_id for fact in snapshot.facts for evidence_id in fact.evidence_ids},
            )
        outcome = self._store.get_outcome(run_id)
        if outcome is not None and getattr(outcome, "outcome_kind", None) == "legacy":
            return (
                set(outcome.facts.allowed_customer_ids),
                set(outcome.facts.allowed_evidence_ids),
            )
        report = snapshot.report
        if report is None:
            return set(), set()
        customers = {customer.customer_id for customer in report.ranked_customers}
        evidence = {
            evidence_id
            for customer in report.ranked_customers
            for evidence_id in customer.evidence_ids
        }
        evidence.update(event.evidence_id for event in report.representative_journeys)
        return customers, evidence


def _versions_from_facts(
    facts: list[AnalysisFact],
    *,
    model_version: str | None,
    generic: bool,
) -> RunVersions:
    dataset_versions: list[str] = []
    adapter_versions: dict[str, str] = {}
    manifest_versions: dict[str, str] = {}
    for fact in facts:
        provenance = fact.payload.provenance
        if provenance.dataset_version not in dataset_versions:
            dataset_versions.append(provenance.dataset_version)
        adapter_versions.update(provenance.adapter_versions)
        manifest_versions.update(provenance.manifest_versions)
    return RunVersions(
        dataset_versions=dataset_versions,
        adapter_versions=adapter_versions,
        manifest_versions=manifest_versions,
        prompt_version="generic-v1" if generic else "legacy-v1",
        model_version=model_version,
    )


__all__ = [
    "RunCoordinator",
    "RunNotCompletedError",
    "RunResourceNotFoundError",
]
