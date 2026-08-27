"""Pure projection from Canonical Run Events to Presentation Intents.

Projectors are pure functions: no clock, no randomness, no I/O.  The same
event log always yields the same intents, so live rendering and replay after
reconnect or restart are provably identical.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from customer_signal.journal.events import CanonicalRunEvent
from customer_signal.presentation.intents import PresentationIntent


class PresentationState(BaseModel):
    """Folded projection state carried between events of one Run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    opened: bool = False
    step_count: int = 0
    values: dict[str, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class PackProjector(Protocol):
    """One Pack's presentation knowledge, kept outside analysis execution."""

    def project(
        self,
        event: CanonicalRunEvent,
        state: PresentationState,
    ) -> tuple[Sequence[PresentationIntent], PresentationState]: ...


def fold_intents(
    projector: PackProjector,
    events: Sequence[CanonicalRunEvent],
) -> list[PresentationIntent]:
    """Replay a Run's presentation from its committed events."""

    state = PresentationState()
    intents: list[PresentationIntent] = []
    for event in events:
        step, state = projector.project(event, state)
        intents.extend(step)
    return intents


__all__ = ["PackProjector", "PresentationState", "fold_intents"]
