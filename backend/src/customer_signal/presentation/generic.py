"""Domain-neutral default projector every Pack inherits.

It renders any Pack's Canonical Run Events with trusted Catalog keys only, so
a brand-new Pack gets a working presentation with zero Frontend changes.  A
Pack overrides projection only when it needs a different arrangement.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from customer_signal.journal.events import CanonicalRunEvent
from customer_signal.presentation.intents import PresentationIntent
from customer_signal.presentation.projector import PresentationState

_ARTIFACT_SURFACES: dict[str, tuple[str, str]] = {
    "goal": ("run.goal", "Card"),
    "plan": ("run.plan", "Table"),
    "fact": ("run.facts", "Timeline"),
    "note": ("run.notes", "Timeline"),
    "report": ("run.report", "Card"),
}


class GenericRunProjector:
    """Pure fold from Canonical Run Events to trusted-catalog intents."""

    def project(
        self,
        event: CanonicalRunEvent,
        state: PresentationState,
    ) -> tuple[Sequence[PresentationIntent], PresentationState]:
        intents: list[PresentationIntent] = []
        next_state = state.model_copy(deep=True)

        if not state.opened:
            intents.append(
                PresentationIntent(
                    surface_key="run",
                    kind="open",
                    catalog_key="Stack",
                    body={"pack_id": event.pack.pack_id, "run_id": str(event.run_id)},
                )
            )
            next_state.opened = True

        kind = event.kind
        payload = event.payload

        if kind == "run.opened":
            intents.append(
                PresentationIntent(
                    surface_key="run",
                    kind="text",
                    body={"text": "분석을 시작했습니다."},
                )
            )
        elif kind == "artifact.committed":
            artifact_kind = str(payload.get("artifact_kind", ""))
            surface = _ARTIFACT_SURFACES.get(artifact_kind)
            if surface is None or event.artifact is None:
                intents.append(
                    PresentationIntent(
                        surface_key="run.diagnostics",
                        kind="notice",
                        catalog_key="Notice",
                        body={
                            "level": "warning",
                            "text": "표현할 수 없는 Artifact를 건너뛰었습니다.",
                            "artifact_kind": artifact_kind,
                        },
                    )
                )
            else:
                surface_key, catalog_key = surface
                intents.append(
                    PresentationIntent(
                        surface_key=surface_key,
                        kind="patch_data",
                        catalog_key=catalog_key,
                        body={
                            "artifact_kind": artifact_kind,
                            "schema_id": event.artifact.schema_id,
                            "value": event.artifact.value,
                        },
                    )
                )
        elif kind == "activity.changed":
            next_state.step_count = state.step_count + 1
            intents.append(
                PresentationIntent(
                    surface_key="run.progress",
                    kind="patch_data",
                    catalog_key="Timeline",
                    body={"sequence": event.sequence, **payload},
                )
            )
        elif kind == "interaction.changed":
            if payload.get("phase") == "requested":
                intents.append(
                    PresentationIntent(
                        surface_key="run.interaction",
                        kind="patch_components",
                        catalog_key="Form",
                        body=dict(payload),
                    )
                )
            else:
                intents.append(
                    PresentationIntent(
                        surface_key="run.interaction",
                        kind="patch_data",
                        catalog_key="Form",
                        body=dict(payload),
                    )
                )
        elif kind == "run.awaiting_input":
            intents.append(
                PresentationIntent(
                    surface_key="run",
                    kind="notice",
                    catalog_key="Notice",
                    body={"level": "info", "text": "추가 입력을 기다리고 있습니다."},
                )
            )
        elif kind == "run.resumed":
            intents.append(
                PresentationIntent(
                    surface_key="run",
                    kind="text",
                    body={"text": "분석을 다시 시작했습니다."},
                )
            )
        elif kind in {"run.completed", "run.degraded", "run.failed"}:
            level = {"run.completed": "success", "run.degraded": "warning"}.get(
                kind, "error"
            )
            body: dict[str, JsonValue] = {
                "level": level,
                "status": payload.get("status", kind.removeprefix("run.")),
                "limitations": payload.get("limitations", []),
            }
            if isinstance(payload.get("error"), dict):
                body["error"] = payload["error"]
            intents.append(
                PresentationIntent(
                    surface_key="run",
                    kind="notice",
                    catalog_key="Notice",
                    body=body,
                )
            )
            intents.append(PresentationIntent(surface_key="run", kind="close"))

        return intents, next_state


__all__ = ["GenericRunProjector"]
