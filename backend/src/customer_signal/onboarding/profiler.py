"""CSV/Parquet table loading and lightweight schema profiling for onboarding."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict, Field

MAX_ROWS = 10_000


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dtype: str
    null_count: int
    distinct_count: int
    top_values: list[str] = Field(default_factory=list)


class TableProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    row_count: int
    columns: list[ColumnProfile]


def load_rows(path: Path) -> tuple[list[str], list[tuple]]:
    """Load every row of a CSV/Parquet file, bounded to keep runs deterministic."""

    if not path.exists():
        raise FileNotFoundError(f"table file not found: {path}")
    reader = {
        ".csv": "read_csv_auto",
        ".parquet": "read_parquet",
    }.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"unsupported table format: {path.suffix} (use .csv or .parquet)")
    with duckdb.connect(":memory:") as connection:
        cursor = connection.execute(f"SELECT * FROM {reader}(?)", [str(path)])
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"table exceeds the onboarding bound of {MAX_ROWS} rows")
    if not rows:
        raise ValueError("table has no rows to onboard")
    return columns, rows


def profile_table(path: Path, *, max_top_values: int = 8) -> TableProfile:
    columns, rows = load_rows(path)
    profiles: list[ColumnProfile] = []
    for index, name in enumerate(columns):
        values = [row[index] for row in rows]
        present = [value for value in values if value is not None]
        counter = Counter(str(value) for value in present)
        dtype = type(present[0]).__name__ if present else "unknown"
        profiles.append(
            ColumnProfile(
                name=name,
                dtype=dtype,
                null_count=len(values) - len(present),
                distinct_count=len(counter),
                top_values=[value for value, _ in counter.most_common(max_top_values)],
            )
        )
    return TableProfile(path=str(path), row_count=len(rows), columns=profiles)


__all__ = ["ColumnProfile", "MAX_ROWS", "TableProfile", "load_rows", "profile_table"]
