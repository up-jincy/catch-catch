"""Pack Kernel: the one execution engine every Analysis Pack runs inside.

The Kernel validates Pack emissions, enforces artifact ordering, normalizes
public errors, and commits Canonical Run Events to the EventJournal.  Nothing
reaches an external stream unless the journal accepted it first.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from customer_signal.domain.analysis import PublicRunError
from customer_signal.journal.events import CanonicalRunEvent, EventDraft, VersionedValue
from customer_signal.journal.journal import EventJournal, SequenceConflictError
from customer_signal.packs.contracts import (
    ActivityDraft,
    AnalysisPackAdapter,
    FactDraft,
    GoalDraft,
    InteractionDraft,
    NoteDraft,
    OutcomeDraft,
    PackContext,
    PackDegraded,
    PackDomainError,
    PlanDraft,
    ReportDraft,
)

type CommittedEventSink = Callable[
    [Sequence[CanonicalRunEvent]], Awaitable[None] | None
]

_CANCELLED_MESSAGE = "분석 실행이 취소됐습니다."
_TIMEOUT_MESSAGE = "분석 시간이 초과됐습니다."
_CONTRACT_MESSAGE = "분석 Pack이 공개 계약을 위반했습니다."
_FAILED_MESSAGE = "분석 실행에 실패했습니다."


class PackInputError(ValueError):
    """Raised before any Run state exists when Pack input is invalid."""


class PackContractViolation(RuntimeError):
    """Internal signal that a Pack emission broke the kernel contract."""


class PackRunResult(BaseModel):
    """Public summary of one kernel-run execution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: UUID
    status: Literal["completed", "degraded", "failed", "awaiting_input"]
    limitations: list[str] = Field(default_factory=list)
    error: PublicRunError | None = None
    last_sequence: int = Field(ge=1)


class _Progress:
    __slots__ = ("goals", "plans", "facts", "notes", "reports", "interaction_requested")

    def __init__(self) -> None:
        self.goals = 0
        self.plans = 0
        self.facts = 0
        self.notes = 0
        self.reports = 0
        self.interaction_requested = False

    def observe(self, event: CanonicalRunEvent) -> None:
        if event.kind == "artifact.committed":
            kind = event.payload.get("artifact_kind")
            if kind == "goal":
                self.goals += 1
            elif kind == "plan":
                self.plans += 1
            elif kind == "fact":
                self.facts += 1
            elif kind == "note":
                self.notes += 1
            elif kind == "report":
                self.reports += 1
        elif event.kind == "interaction.changed":
            self.interaction_requested = event.payload.get("phase") == "requested"


class PackKernel:
    """Run Packs against the journal with shared validation and error rules."""

    def __init__(self, journal: EventJournal, *, timeout_seconds: float = 240.0) -> None:
        if not 0 < timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be positive and bounded")
        self._journal = journal
        self._timeout_seconds = timeout_seconds

    @property
    def journal(self) -> EventJournal:
        return self._journal

    async def run(
        self,
        pack: AnalysisPackAdapter,
        request_data: BaseModel | dict[str, JsonValue],
        *,
        run_id: UUID,
        options: dict[str, JsonValue] | None = None,
        resume_payload: dict[str, JsonValue] | None = None,
        on_committed: CommittedEventSink | None = None,
    ) -> PackRunResult:
        request = self._validate_input(pack, request_data)
        pack_ref = pack.spec.ref
        progress = _Progress()

        if resume_payload is None:
            opened = await self._journal.create(
                run_id,
                EventDraft(
                    kind="run.opened",
                    pack=pack_ref,
                    payload={
                        "status": "running",
                        "input": request.model_dump(mode="json"),
                    },
                ),
                idempotency_key=f"open:{run_id}",
            )
            expected_sequence = opened.sequence
            progress.observe(opened)
        else:
            async for event in self._journal.read(run_id):
                progress.observe(event)
            expected_sequence = await self._journal.last_sequence(run_id)

        context = PackContext(
            run_id=run_id,
            options=dict(options or {}),
            resumed=resume_payload is not None,
        )
        outcome: OutcomeDraft | None = None
        iterator = pack.execute(request, context)
        try:
            try:
                if resume_payload is None:
                    await _notify(on_committed, (opened,))
                else:
                    resumed_events = await self._append(
                        run_id,
                        expected_sequence,
                        [
                            EventDraft(
                                kind="interaction.changed",
                                pack=pack_ref,
                                payload={"phase": "answered", **resume_payload},
                            ),
                            EventDraft(kind="run.resumed", pack=pack_ref),
                        ],
                    )
                    expected_sequence = resumed_events[-1].sequence
                    progress.observe(resumed_events[0])
                    await _notify(on_committed, resumed_events)
                async with asyncio.timeout(self._timeout_seconds):
                    async for emission in iterator:
                        drafts, next_outcome = self._drafts_for(pack, emission, progress)
                        if drafts:
                            committed = await self._append(run_id, expected_sequence, drafts)
                            expected_sequence = committed[-1].sequence
                            for event in committed:
                                progress.observe(event)
                            await _notify(on_committed, committed)
                        if next_outcome is not None:
                            outcome = next_outcome
                            break
            finally:
                closer = getattr(iterator, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:
                        pass
        except asyncio.CancelledError:
            error = PublicRunError(code="run_cancelled", message=_CANCELLED_MESSAGE)
            await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )
            raise
        except TimeoutError:
            error = PublicRunError(code="analysis_timeout", message=_TIMEOUT_MESSAGE)
            return await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )
        except PackDegraded as degraded:
            outcome = OutcomeDraft(
                status="degraded",
                limitations=list(degraded.limitations)
                or ["분석 결과가 없어 제한적으로 종료했습니다."],
            )
        except PackDomainError as domain_error:
            outcome = OutcomeDraft(status="failed", error=domain_error.public_error)
        except (PackContractViolation, ValidationError):
            error = PublicRunError(
                code="analysis_pack_contract_violation",
                message=_CONTRACT_MESSAGE,
            )
            return await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )
        except Exception:
            error = PublicRunError(code="analysis_pack_failed", message=_FAILED_MESSAGE)
            return await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )

        if outcome is None:
            error = PublicRunError(
                code="analysis_pack_contract_violation",
                message=_CONTRACT_MESSAGE,
            )
            return await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )
        return await _finish_even_if_cancelled(
            self._commit_outcome(
                run_id, expected_sequence, pack_ref, outcome, progress, on_committed
            )
        )

    def _validate_input(
        self,
        pack: AnalysisPackAdapter,
        request_data: BaseModel | dict[str, JsonValue],
    ) -> BaseModel:
        if isinstance(request_data, pack.Input):
            return request_data
        if isinstance(request_data, BaseModel):
            request_data = request_data.model_dump(mode="json")
        try:
            return pack.Input.model_validate_json(_canonical_json(request_data))
        except ValidationError as error:
            raise PackInputError(str(error)) from error

    def _drafts_for(
        self,
        pack: AnalysisPackAdapter,
        emission: object,
        progress: _Progress,
    ) -> tuple[list[EventDraft], OutcomeDraft | None]:
        pack_ref = pack.spec.ref
        match emission:
            case GoalDraft():
                return [self._artifact_draft(pack, "goal", emission.value, {})], None
            case PlanDraft():
                if progress.goals < 1:
                    raise PackContractViolation("plan requires a committed goal")
                if emission.revised and progress.plans < 1:
                    raise PackContractViolation("plan revision requires a prior plan")
                return [
                    self._artifact_draft(
                        pack, "plan", emission.value, {"revised": emission.revised}
                    )
                ], None
            case FactDraft():
                if progress.plans < 1:
                    raise PackContractViolation("fact requires a committed plan")
                extras: dict[str, JsonValue] = {}
                if emission.step_id is not None:
                    extras["step_id"] = emission.step_id
                return [self._artifact_draft(pack, "fact", emission.value, extras)], None
            case NoteDraft():
                if progress.facts < 1:
                    raise PackContractViolation("note requires a committed fact")
                return [self._artifact_draft(pack, "note", emission.value, {})], None
            case ReportDraft():
                if progress.facts < 1:
                    raise PackContractViolation("report requires a committed fact")
                return [
                    self._artifact_draft(pack, "report", emission.value, dict(emission.meta))
                ], None
            case ActivityDraft():
                return [
                    EventDraft(
                        kind="activity.changed",
                        pack=pack_ref,
                        payload=dict(emission.payload),
                    )
                ], None
            case InteractionDraft():
                return [
                    EventDraft(
                        kind="interaction.changed",
                        pack=pack_ref,
                        payload={"phase": emission.phase, **emission.payload},
                    )
                ], None
            case OutcomeDraft():
                return [], emission
            case _:
                raise PackContractViolation(
                    f"unknown pack emission: {type(emission).__name__}"
                )

    def _artifact_draft(
        self,
        pack: AnalysisPackAdapter,
        kind: Literal["goal", "plan", "fact", "note", "report"],
        value: dict[str, JsonValue],
        extras: dict[str, JsonValue],
    ) -> EventDraft:
        try:
            schema = pack.spec.schema_for(kind)
        except KeyError as error:
            raise PackContractViolation(str(error)) from error
        validated = schema.model.model_validate_json(_canonical_json(value))
        return EventDraft(
            kind="artifact.committed",
            pack=pack.spec.ref,
            artifact=VersionedValue(
                schema_id=schema.schema_id,
                schema_digest=schema.digest,
                value=validated.model_dump(mode="json"),
            ),
            payload={"artifact_kind": kind, **extras},
        )

    async def _commit_outcome(
        self,
        run_id: UUID,
        expected_sequence: int,
        pack_ref,
        outcome: OutcomeDraft,
        progress: _Progress,
        on_committed: CommittedEventSink | None,
    ) -> PackRunResult:
        if outcome.status == "completed" and progress.reports < 1:
            error = PublicRunError(
                code="analysis_pack_contract_violation",
                message=_CONTRACT_MESSAGE,
            )
            return await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )
        if outcome.status == "awaiting_input" and not progress.interaction_requested:
            error = PublicRunError(
                code="analysis_pack_contract_violation",
                message=_CONTRACT_MESSAGE,
            )
            return await self._terminal_failure(
                run_id, expected_sequence, pack_ref, error, on_committed
            )

        if outcome.status == "awaiting_input":
            draft = EventDraft(
                kind="run.awaiting_input",
                pack=pack_ref,
                payload={"status": "awaiting_clarification"},
            )
        else:
            payload: dict[str, JsonValue] = {
                "status": outcome.status,
                "limitations": list(outcome.limitations),
            }
            if outcome.error is not None:
                payload["error"] = outcome.error.model_dump(mode="json")
            draft = EventDraft(
                kind=f"run.{outcome.status}",  # type: ignore[arg-type]
                pack=pack_ref,
                payload=payload,
            )
        committed = await self._append(
            run_id, expected_sequence, [draft], reload_on_conflict=True
        )
        await _notify(on_committed, committed)
        return PackRunResult(
            run_id=run_id,
            status=outcome.status,
            limitations=list(outcome.limitations),
            error=outcome.error,
            last_sequence=committed[-1].sequence,
        )

    async def _terminal_failure(
        self,
        run_id: UUID,
        expected_sequence: int,
        pack_ref,
        error: PublicRunError,
        on_committed: CommittedEventSink | None,
    ) -> PackRunResult:
        committed = await self._append(
            run_id,
            expected_sequence,
            [
                EventDraft(
                    kind="run.failed",
                    pack=pack_ref,
                    payload={
                        "status": "failed",
                        "limitations": [],
                        "error": error.model_dump(mode="json"),
                    },
                )
            ],
            reload_on_conflict=True,
        )
        await _notify(on_committed, committed)
        return PackRunResult(
            run_id=run_id,
            status="failed",
            error=error,
            last_sequence=committed[-1].sequence,
        )

    async def _append(
        self,
        run_id: UUID,
        expected_sequence: int,
        drafts: Sequence[EventDraft],
        *,
        reload_on_conflict: bool = False,
    ) -> tuple[CanonicalRunEvent, ...]:
        try:
            return await self._journal.append(run_id, expected_sequence, drafts)
        except SequenceConflictError as conflict:
            if not reload_on_conflict:
                raise
            return await self._journal.append(run_id, conflict.latest_sequence, drafts)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def _finish_even_if_cancelled(coro):
    """Complete an in-flight terminal commit even when the caller is cancelled.

    A terminal event that already reached the journal must not leave the
    kernel reporting failure: the commit finishes and the result is returned,
    so the caller records a state consistent with the journal.
    """

    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            return await task
        except BaseException:
            raise cancelled from None


async def _notify(
    sink: CommittedEventSink | None,
    events: Sequence[CanonicalRunEvent],
) -> None:
    if sink is None:
        return
    pending = sink(events)
    if inspect.isawaitable(pending):
        await pending


__all__ = [
    "PackInputError",
    "PackKernel",
    "PackRunResult",
    "PackContractViolation",
]
