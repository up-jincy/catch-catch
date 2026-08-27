"""Explicit Analysis Pack registry with fail-fast startup validation.

Packs are never auto-discovered.  The composition root lists every Pack, and
this registry rejects a broken composition before the server accepts traffic.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from pydantic import BaseModel

from customer_signal.packs.contracts import AnalysisPackAdapter


class PackRegistrationError(ValueError):
    """Raised at composition time when a Pack cannot be registered safely."""


class UnknownPackError(LookupError):
    """Raised when a pack_id has not been registered."""


class AnalysisPackRegistry:
    """Immutable mapping of ``pack_id`` to one validated Analysis Pack."""

    def __init__(self, packs: Iterable[AnalysisPackAdapter]) -> None:
        self._packs: dict[str, AnalysisPackAdapter] = {}
        seen_instances: list[int] = []
        seen_schema_digests: dict[str, str] = {}
        for pack in packs:
            self._validate_pack(pack, seen_instances, seen_schema_digests)
            self._packs[pack.spec.pack_id] = pack
            seen_instances.append(id(pack))

    def get(self, pack_id: str) -> AnalysisPackAdapter:
        try:
            return self._packs[pack_id]
        except KeyError as error:
            raise UnknownPackError(f"analysis pack not found: {pack_id}") from error

    def __contains__(self, pack_id: str) -> bool:
        return pack_id in self._packs

    def __iter__(self) -> Iterator[AnalysisPackAdapter]:
        return iter(self._packs.values())

    def __len__(self) -> int:
        return len(self._packs)

    def pack_ids(self) -> tuple[str, ...]:
        return tuple(self._packs)

    def _validate_pack(
        self,
        pack: AnalysisPackAdapter,
        seen_instances: list[int],
        seen_schema_digests: dict[str, str],
    ) -> None:
        if id(pack) in seen_instances:
            raise PackRegistrationError("the same Pack instance cannot register twice")
        spec = getattr(pack, "spec", None)
        if spec is None:
            raise PackRegistrationError("Pack must declare a spec")
        if spec.pack_id in self._packs:
            raise PackRegistrationError(f"duplicate pack_id: {spec.pack_id}")

        input_model = getattr(pack, "Input", None)
        if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
            raise PackRegistrationError(f"{spec.pack_id}: Input must be a Pydantic model")
        self._require_json_schema(spec.pack_id, spec.input_schema_id, input_model)

        for schema in spec.artifact_schemas:
            self._require_json_schema(spec.pack_id, schema.schema_id, schema.model)
            digest = schema.digest
            existing = seen_schema_digests.get(schema.schema_id)
            if existing is not None and existing != digest:
                raise PackRegistrationError(
                    f"{spec.pack_id}: schema_id {schema.schema_id} conflicts with an "
                    "already registered schema digest"
                )
            seen_schema_digests[schema.schema_id] = digest

    @staticmethod
    def _require_json_schema(pack_id: str, schema_id: str, model: type[BaseModel]) -> None:
        try:
            json.dumps(model.model_json_schema())
        except (TypeError, ValueError) as error:
            raise PackRegistrationError(
                f"{pack_id}: schema {schema_id} is not JSON-serializable"
            ) from error


__all__ = [
    "AnalysisPackRegistry",
    "PackRegistrationError",
    "UnknownPackError",
]
