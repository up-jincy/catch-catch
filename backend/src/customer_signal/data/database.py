"""Atomic DuckDB creation for the deterministic customer-signal dataset."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import duckdb

from customer_signal.domain.models import SyntheticDataset
from customer_signal.synthetic.manifest import SYNTHETIC_MANIFEST_VERSION


DATABASE_SCHEMA_VERSION = 2
SYNTHETIC_DATASET_VERSION = 2

_SCHEMA = """
CREATE TABLE database_metadata (
    schema_version INTEGER NOT NULL,
    dataset_version INTEGER NOT NULL,
    manifest_version VARCHAR NOT NULL
);

CREATE TABLE customers (
    customer_id VARCHAR PRIMARY KEY
);

CREATE TABLE evidence (
    evidence_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    masked_customer_id VARCHAR NOT NULL,
    summary VARCHAR NOT NULL,
    raw_fields JSON NOT NULL
);

CREATE TABLE events (
    event_id VARCHAR PRIMARY KEY,
    evidence_id VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    topic VARCHAR NOT NULL,
    outcome VARCHAR NOT NULL,
    text VARCHAR NOT NULL,
    identities JSON NOT NULL,
    canonical_customer_id VARCHAR NOT NULL,
    attributes JSON NOT NULL,
    dimensions JSON NOT NULL,
    measures JSON NOT NULL
);

CREATE TABLE identity_edges (
    left_namespace VARCHAR NOT NULL,
    left_value VARCHAR NOT NULL,
    right_namespace VARCHAR NOT NULL,
    right_value VARCHAR NOT NULL,
    link_type VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL,
    provenance VARCHAR NOT NULL,
    PRIMARY KEY (
        left_namespace,
        left_value,
        right_namespace,
        right_value,
        link_type
    )
);
"""

_REQUIRED_COLUMN_TYPES = {
    "database_metadata": {
        "schema_version": "INTEGER",
        "dataset_version": "INTEGER",
        "manifest_version": "VARCHAR",
    },
    "customers": {"customer_id": "VARCHAR"},
    "evidence": {
        "evidence_id": "VARCHAR",
        "source_id": "VARCHAR",
        "occurred_at": "TIMESTAMP WITH TIME ZONE",
        "masked_customer_id": "VARCHAR",
        "summary": "VARCHAR",
        "raw_fields": "JSON",
    },
    "events": {
        "event_id": "VARCHAR",
        "evidence_id": "VARCHAR",
        "source_id": "VARCHAR",
        "occurred_at": "TIMESTAMP WITH TIME ZONE",
        "event_type": "VARCHAR",
        "action": "VARCHAR",
        "topic": "VARCHAR",
        "outcome": "VARCHAR",
        "text": "VARCHAR",
        "identities": "JSON",
        "canonical_customer_id": "VARCHAR",
        "attributes": "JSON",
        "dimensions": "JSON",
        "measures": "JSON",
    },
    "identity_edges": {
        "left_namespace": "VARCHAR",
        "left_value": "VARCHAR",
        "right_namespace": "VARCHAR",
        "right_value": "VARCHAR",
        "link_type": "VARCHAR",
        "confidence": "DOUBLE",
        "provenance": "VARCHAR",
    },
}
_REQUIRED_TABLES = frozenset(_REQUIRED_COLUMN_TYPES)
_REQUIRED_SOURCE_IDS = frozenset(
    {
        "search_history",
        "search_feedback",
        "digital_behavior",
        "subscription",
        "voc",
    }
)
_REQUIRED_LINK_TYPES = frozenset({"EXACT", "DECLARED", "SYNTHETIC"})
_REQUIRED_ROW_COUNTS = {
    "customers": 30,
    "events": 199,
    "evidence": 199,
    "identity_edges": 150,
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write_database(path: Path, dataset: SyntheticDataset) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(_SCHEMA)
        connection.execute(
            "INSERT INTO database_metadata VALUES (?, ?, ?)",
            [
                DATABASE_SCHEMA_VERSION,
                SYNTHETIC_DATASET_VERSION,
                SYNTHETIC_MANIFEST_VERSION,
            ],
        )
        connection.executemany(
            "INSERT INTO customers VALUES (?)",
            [(customer_id,) for customer_id in dataset.customers],
        )
        connection.executemany(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    record.evidence_id,
                    record.source_id,
                    record.occurred_at,
                    record.masked_customer_id,
                    record.summary,
                    _json(record.raw_fields),
                )
                for record in dataset.evidence
            ],
        )
        connection.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    event.event_id,
                    event.evidence_id,
                    event.source_id,
                    event.occurred_at,
                    event.event_type,
                    event.action,
                    event.topic,
                    event.outcome,
                    event.text,
                    _json([identity.model_dump(mode="json") for identity in event.identities]),
                    event.canonical_customer_id,
                    _json(event.attributes),
                    _json(event.dimensions),
                    _json(event.measures),
                )
                for event in dataset.events
            ],
        )
        connection.executemany(
            "INSERT INTO identity_edges VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    edge.left.namespace,
                    edge.left.value,
                    edge.right.namespace,
                    edge.right.value,
                    edge.link_type,
                    edge.confidence,
                    edge.provenance,
                )
                for edge in dataset.identity_edges
            ],
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


def is_database_ready(path: str | Path) -> bool:
    """Return whether ``path`` is the complete current managed demo database."""

    database_path = Path(path)
    if not database_path.is_file():
        return False

    connection = None
    try:
        connection = duckdb.connect(str(database_path), read_only=True)
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if tables != _REQUIRED_TABLES:
            return False

        versions = connection.execute(
            "SELECT schema_version, dataset_version, manifest_version FROM database_metadata"
        ).fetchall()
        if versions != [
            (
                DATABASE_SCHEMA_VERSION,
                SYNTHETIC_DATASET_VERSION,
                SYNTHETIC_MANIFEST_VERSION,
            )
        ]:
            return False

        for table, required_column_types in _REQUIRED_COLUMN_TYPES.items():
            column_types = {
                row[0]: row[1] for row in connection.execute(f"DESCRIBE {table}").fetchall()
            }
            if column_types != required_column_types:
                return False

        row_counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in _REQUIRED_ROW_COUNTS
        }
        if row_counts != _REQUIRED_ROW_COUNTS:
            return False

        source_ids = {
            row[0] for row in connection.execute("SELECT DISTINCT source_id FROM events").fetchall()
        }
        if source_ids != _REQUIRED_SOURCE_IDS:
            return False

        link_types = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT link_type FROM identity_edges"
            ).fetchall()
        }
        if link_types != _REQUIRED_LINK_TYPES:
            return False

        identityless_events = connection.execute(
            "SELECT count(*) FROM events WHERE json_array_length(identities) = 0"
        ).fetchone()[0]
        return identityless_events == 0
    except (duckdb.Error, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            connection.close()


def seed_database(path: str | Path, dataset: SyntheticDataset) -> Path:
    """Write ``dataset`` to a sibling temporary database, then atomically replace ``path``."""

    if isinstance(path, str) and not path.strip():
        raise ValueError("database path must be explicit")

    destination = Path(path)
    if destination.exists() and destination.is_dir():
        raise IsADirectoryError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()

    try:
        _write_database(temporary_path, dataset)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)

    return destination
