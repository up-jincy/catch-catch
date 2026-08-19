"""Atomic DuckDB creation for the deterministic customer-signal dataset."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import duckdb

from customer_signal.domain.models import SyntheticDataset


_SCHEMA = """
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
    canonical_customer_id VARCHAR NOT NULL,
    attributes JSON NOT NULL
);

CREATE TABLE ground_truth (
    customer_id VARCHAR PRIMARY KEY
);
"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write_database(path: Path, dataset: SyntheticDataset) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(_SCHEMA)
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
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    event.canonical_customer_id,
                    _json(event.attributes),
                )
                for event in dataset.events
            ],
        )
        connection.executemany(
            "INSERT INTO ground_truth VALUES (?)",
            [(customer_id,) for customer_id in dataset.ground_truth_customer_ids],
        )
        connection.execute("COMMIT")
    finally:
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
