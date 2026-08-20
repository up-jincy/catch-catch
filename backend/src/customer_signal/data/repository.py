"""Focused, read-only DuckDB access for canonical events and evidence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from customer_signal.domain.models import CustomerEvent, EvidenceRecord, IdentityEdge, SourceId


SOURCE_IDS = (
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
)
_SOURCE_ID_SET = frozenset(SOURCE_IDS)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class DatabaseNotFoundError(FileNotFoundError):
    """Raised when the configured DuckDB file does not exist."""


class EntityNotFoundError(LookupError):
    """Raised when a requested customer or evidence record does not exist."""


class SourceCatalogEntry(BaseModel):
    """Availability summary for one canonical source inside a requested time range."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: SourceId
    start_at: AwareDatetime
    end_at: AwareDatetime
    row_count: int = Field(ge=0)


def _validate_time_range(start_at: datetime, end_at: datetime) -> None:
    valid = (
        isinstance(start_at, datetime)
        and isinstance(end_at, datetime)
        and start_at.tzinfo is not None
        and end_at.tzinfo is not None
        and start_at.utcoffset() is not None
        and end_at.utcoffset() is not None
        and start_at < end_at
    )
    if not valid:
        raise ValueError(
            "start_at and end_at must be timezone-aware and start_at must be before end_at"
        )


def _validate_sources(enabled_sources: Sequence[str]) -> list[str]:
    if isinstance(enabled_sources, (str, bytes)) or not isinstance(enabled_sources, Sequence):
        raise ValueError("enabled_sources must be a non-empty unique source allowlist")

    sources = list(enabled_sources)
    if (
        not sources
        or any(not isinstance(source, str) for source in sources)
        or len(sources) != len(set(sources))
        or any(source not in _SOURCE_ID_SET for source in sources)
    ):
        raise ValueError("enabled_sources must be a non-empty unique source allowlist")
    return sources


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")


def _validate_evidence_ids(evidence_ids: Sequence[str]) -> list[str]:
    if isinstance(evidence_ids, (str, bytes)) or not isinstance(evidence_ids, Sequence):
        raise ValueError("evidence_ids must be a non-empty sequence")

    identifiers = list(evidence_ids)
    if not identifiers or any(
        not isinstance(identifier, str) or not identifier for identifier in identifiers
    ):
        raise ValueError("evidence_ids must be a non-empty sequence")
    return identifiers


def _parse_json(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("stored JSON value must be an object")
        return parsed
    return value


def _from_epoch_microseconds(value: int) -> datetime:
    return _UNIX_EPOCH + timedelta(microseconds=value)


class DuckDBRepository:
    """Read canonical data through bounded queries and per-call read-only connections."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _connect(self):
        if not self._path.is_file():
            raise DatabaseNotFoundError(f"database not found: {self._path}")
        return duckdb.connect(str(self._path), read_only=True)

    def catalog_sources(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> list[SourceCatalogEntry]:
        """Return source availability within the half-open ``[start_at, end_at)`` range."""

        _validate_time_range(start_at, end_at)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT
                    source_id,
                    min(epoch_us(occurred_at)),
                    max(epoch_us(occurred_at)),
                    count(*)
                FROM events
                WHERE occurred_at >= ? AND occurred_at < ?
                GROUP BY source_id
                ORDER BY CASE source_id
                    WHEN 'search_history' THEN 1
                    WHEN 'search_feedback' THEN 2
                    WHEN 'digital_behavior' THEN 3
                    WHEN 'subscription' THEN 4
                    WHEN 'voc' THEN 5
                END
                """,
                [start_at, end_at],
            ).fetchall()
        finally:
            connection.close()

        return [
            SourceCatalogEntry.model_validate(
                {
                    "source_id": row[0],
                    "start_at": _from_epoch_microseconds(row[1]),
                    "end_at": _from_epoch_microseconds(row[2]),
                    "row_count": row[3],
                },
                strict=True,
            )
            for row in rows
        ]

    def list_events(
        self,
        start_at: datetime,
        end_at: datetime,
        enabled_sources: Sequence[str],
        customer_id: str | None = None,
        limit: int = 100,
    ) -> list[CustomerEvent]:
        """Return bounded canonical events in chronological order."""

        _validate_time_range(start_at, end_at)
        sources = _validate_sources(enabled_sources)
        _validate_limit(limit)

        connection = self._connect()
        try:
            if customer_id is not None:
                exists = connection.execute(
                    "SELECT 1 FROM customers WHERE customer_id = ?",
                    [customer_id],
                ).fetchone()
                if exists is None:
                    raise EntityNotFoundError(f"customer not found: {customer_id}")

            placeholders = ", ".join("?" for _ in sources)
            customer_filter = ""
            parameters: list[Any] = [start_at, end_at, *sources]
            if customer_id is not None:
                customer_filter = " AND canonical_customer_id = ?"
                parameters.append(customer_id)
            parameters.append(limit)

            rows = connection.execute(
                f"""
                SELECT
                    event_id,
                    evidence_id,
                    source_id,
                    epoch_us(occurred_at),
                    event_type,
                    action,
                    topic,
                    outcome,
                    text,
                    identities,
                    canonical_customer_id,
                    attributes
                FROM events
                WHERE occurred_at >= ?
                  AND occurred_at < ?
                  AND source_id IN ({placeholders})
                  {customer_filter}
                ORDER BY occurred_at, event_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        finally:
            connection.close()

        return [
            CustomerEvent.model_validate(
                {
                    "event_id": row[0],
                    "evidence_id": row[1],
                    "source_id": row[2],
                    "occurred_at": _from_epoch_microseconds(row[3]),
                    "event_type": row[4],
                    "action": row[5],
                    "topic": row[6],
                    "outcome": row[7],
                    "text": row[8],
                    "identities": (json.loads(row[9]) if isinstance(row[9], str) else row[9]),
                    "canonical_customer_id": row[10],
                    "attributes": _parse_json(row[11]),
                },
                strict=True,
            )
            for row in rows
        ]

    def get_evidence(self, evidence_ids: Sequence[str]) -> list[EvidenceRecord]:
        """Return masked evidence records in the caller's requested identifier order."""

        identifiers = _validate_evidence_ids(evidence_ids)
        placeholders = ", ".join("?" for _ in identifiers)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT
                    evidence_id,
                    source_id,
                    epoch_us(occurred_at),
                    masked_customer_id,
                    summary,
                    raw_fields
                FROM evidence
                WHERE evidence_id IN ({placeholders})
                """,
                identifiers,
            ).fetchall()
        finally:
            connection.close()

        records = {
            row[0]: EvidenceRecord.model_validate(
                {
                    "evidence_id": row[0],
                    "source_id": row[1],
                    "occurred_at": _from_epoch_microseconds(row[2]),
                    "masked_customer_id": row[3],
                    "summary": row[4],
                    "raw_fields": _parse_json(row[5]),
                },
                strict=True,
            )
            for row in rows
        }
        missing = [identifier for identifier in identifiers if identifier not in records]
        if missing:
            raise EntityNotFoundError(f"evidence not found: {', '.join(missing)}")
        return [records[identifier] for identifier in identifiers]

    def list_identity_edges(
        self,
        start_at: datetime,
        end_at: datetime,
        enabled_sources: Sequence[str],
        limit: int = 100,
    ) -> list[IdentityEdge]:
        """Return ordered graph components for the same bounded Event selection."""

        _validate_time_range(start_at, end_at)
        sources = _validate_sources(enabled_sources)
        _validate_limit(limit)
        placeholders = ", ".join("?" for _ in sources)
        connection = self._connect()
        try:
            event_rows = connection.execute(
                f"""
                SELECT identities
                FROM events
                WHERE occurred_at >= ?
                  AND occurred_at < ?
                  AND source_id IN ({placeholders})
                ORDER BY occurred_at, event_id
                LIMIT ?
                """,
                [start_at, end_at, *sources, limit],
            ).fetchall()

            seeds: set[tuple[str, str]] = set()
            for (identities,) in event_rows:
                parsed = json.loads(identities) if isinstance(identities, str) else identities
                seeds.update((identity["namespace"], identity["value"]) for identity in parsed)
            if not seeds:
                return []

            seed_values = ", ".join("(?, ?)" for _ in seeds)
            seed_parameters = [value for seed in sorted(seeds) for value in seed]
            edge_rows = connection.execute(
                f"""
                WITH RECURSIVE
                seed(namespace, value) AS (
                    VALUES {seed_values}
                ),
                oriented_edges(namespace, value, next_namespace, next_value) AS (
                    SELECT
                        left_namespace,
                        left_value,
                        right_namespace,
                        right_value
                    FROM identity_edges
                    UNION ALL
                    SELECT
                        right_namespace,
                        right_value,
                        left_namespace,
                        left_value
                    FROM identity_edges
                ),
                reachable(namespace, value) AS (
                    SELECT namespace, value FROM seed
                    UNION
                    SELECT next_namespace, next_value
                    FROM reachable
                    JOIN oriented_edges
                      ON reachable.namespace = oriented_edges.namespace
                     AND reachable.value = oriented_edges.value
                )
                SELECT
                    identity_edges.left_namespace,
                    identity_edges.left_value,
                    identity_edges.right_namespace,
                    identity_edges.right_value,
                    identity_edges.link_type,
                    identity_edges.confidence,
                    identity_edges.provenance
                FROM identity_edges
                JOIN reachable AS left_node
                  ON identity_edges.left_namespace = left_node.namespace
                 AND identity_edges.left_value = left_node.value
                JOIN reachable AS right_node
                  ON identity_edges.right_namespace = right_node.namespace
                 AND identity_edges.right_value = right_node.value
                ORDER BY
                    identity_edges.left_namespace,
                    identity_edges.left_value,
                    identity_edges.right_namespace,
                    identity_edges.right_value,
                    identity_edges.link_type
                """,
                seed_parameters,
            ).fetchall()
        finally:
            connection.close()

        return [
            IdentityEdge.model_validate(
                {
                    "left": {"namespace": row[0], "value": row[1]},
                    "right": {"namespace": row[2], "value": row[3]},
                    "link_type": row[4],
                    "confidence": row[5],
                    "provenance": row[6],
                },
                strict=True,
            )
            for row in edge_rows
        ]


__all__ = [
    "DatabaseNotFoundError",
    "DuckDBRepository",
    "EntityNotFoundError",
    "SourceCatalogEntry",
]
