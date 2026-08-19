"""Explicit-path CLI for seeding the deterministic DuckDB database."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from customer_signal.data.database import seed_database
from customer_signal.synthetic.generator import generate_dataset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed the customer-signal DuckDB database.")
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="Exact DuckDB destination path (required).",
    )
    parser.add_argument("--seed", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate the requested dataset and atomically seed its explicit destination."""

    args = _parser().parse_args(argv)
    seed_database(args.database, generate_dataset(seed=args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
