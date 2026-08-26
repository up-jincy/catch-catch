"""Onboarding CLI: draft a mapping spec from a raw table, then validate and register it."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from customer_signal.data.source_registry import validate_adapter_contract
from customer_signal.domain.sources import EventScope
from customer_signal.onboarding.adapter import MappedTableAdapter
from customer_signal.onboarding.draft import gemini_spec, heuristic_spec
from customer_signal.onboarding.profiler import MAX_ROWS, profile_table
from customer_signal.onboarding.spec import SourceMappingSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customer_signal.onboarding",
        description="Onboard a raw CSV/Parquet table as a canonical event source.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    draft = commands.add_parser("draft", help="Profile a table and write a spec draft.")
    draft.add_argument("--file", type=Path, required=True, help="CSV/Parquet input table.")
    draft.add_argument("--source-id", required=True)
    draft.add_argument("--label", required=True)
    draft.add_argument("--description", required=True)
    draft.add_argument("--out", type=Path, required=True, help="Spec draft JSON destination.")
    draft.add_argument(
        "--gemini",
        action="store_true",
        help="Draft with Gemini (needs GEMINI_API_KEY) instead of name/type heuristics.",
    )

    register = commands.add_parser(
        "register", help="Validate a reviewed spec against its table and register both."
    )
    register.add_argument("--spec", type=Path, required=True)
    register.add_argument("--file", type=Path, required=True)
    register.add_argument(
        "--registry-dir",
        type=Path,
        default=Path("data/onboarded-sources"),
        help="Directory the API scans at startup.",
    )
    return parser


def _draft(args: argparse.Namespace) -> int:
    profile = profile_table(args.file)
    if args.gemini:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            print("GEMINI_API_KEY is not set; rerun without --gemini for heuristics.")
            return 1
        spec = asyncio.run(
            gemini_spec(
                profile,
                source_id=args.source_id,
                label=args.label,
                description=args.description,
                api_key=api_key,
            )
        )
    else:
        spec = heuristic_spec(
            profile,
            source_id=args.source_id,
            label=args.label,
            description=args.description,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Draft written to {args.out} — review the mapping and PII fields, then register.")
    return 0


def _register(args: argparse.Namespace) -> int:
    spec = SourceMappingSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    adapter = MappedTableAdapter.from_file(spec, args.file)
    manifest = adapter.describe()
    validate_adapter_contract(
        adapter,
        EventScope(
            source_ids=[manifest.source_id],
            start_at=manifest.data_interval.start_at,
            end_at=manifest.data_interval.end_at,
            max_events=MAX_ROWS,
        ),
    )

    pii_fields = {
        name: item.pii_classification
        for name, item in spec.dimensions.items()
        if item.pii_classification != "none"
    }
    print(f"Source {manifest.source_id}: {len(adapter.evidence_by_id)} events validated.")
    print(f"  event_types: {sorted(manifest.supported_event_types)}")
    print(f"  PII fields: {json.dumps(pii_fields, ensure_ascii=False) if pii_fields else 'none'}")

    target = args.registry_dir / manifest.source_id
    target.mkdir(parents=True, exist_ok=True)
    approved = spec.model_copy(update={"status": "approved"})
    (target / "spec.json").write_text(approved.model_dump_json(indent=2) + "\n", encoding="utf-8")
    shutil.copy2(args.file, target / f"data{args.file.suffix.lower()}")
    print(f"Registered under {target} — restart the API to pick it up.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "draft":
        return _draft(args)
    return _register(args)


if __name__ == "__main__":
    sys.exit(main())
