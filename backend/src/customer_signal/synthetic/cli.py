"""Command-line JSON export for the deterministic synthetic dataset."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from customer_signal.synthetic.generator import generate_dataset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate synthetic customer Journey JSON.")
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Serialize generated JSON to stdout or an explicitly requested path."""

    args = _parser().parse_args(argv)
    payload = generate_dataset(seed=args.seed).model_dump_json(indent=2)
    if args.output is None:
        sys.stdout.write(f"{payload}\n")
    else:
        args.output.write_text(f"{payload}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
