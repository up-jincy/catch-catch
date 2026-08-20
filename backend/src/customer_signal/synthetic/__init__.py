"""Deterministic synthetic customer journey data."""

from customer_signal.synthetic.adapter import SyntheticDuckDBAdapter
from customer_signal.synthetic.generator import generate_dataset
from customer_signal.synthetic.manifest import synthetic_source_manifest

__all__ = ["SyntheticDuckDBAdapter", "generate_dataset", "synthetic_source_manifest"]
