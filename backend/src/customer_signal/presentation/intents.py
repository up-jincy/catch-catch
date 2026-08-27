"""PresentationIntent: how a Run wants to be shown, independent of any protocol.

Intents are derived state.  They are recomputed from Canonical Run Events, are
never the source of truth, and never change Run lifecycle.  A concrete UI
protocol adapter (A2UI, plain JSON, a future host) encodes intents separately.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

type PresentationIntentKind = Literal[
    "open",
    "patch_components",
    "patch_data",
    "close",
    "text",
    "notice",
]

# The first trusted, domain-neutral Catalog vocabulary.  Packs may only point
# at these keys; renderer implementations belong to the Frontend host.
TRUSTED_CATALOG_KEYS: frozenset[str] = frozenset(
    {
        "Text",
        "Stack",
        "Grid",
        "Card",
        "Metric",
        "Table",
        "Chart",
        "Timeline",
        "Form",
        "Select",
        "Button",
        "EvidenceLink",
        "Notice",
    }
)


class PresentationIntent(BaseModel):
    """One protocol-neutral instruction for a presentation surface."""

    model_config = ConfigDict(extra="forbid", strict=True)

    surface_key: str = Field(min_length=1, max_length=128)
    kind: PresentationIntentKind
    catalog_key: str | None = None
    body: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "PresentationIntent",
    "PresentationIntentKind",
    "TRUSTED_CATALOG_KEYS",
]
