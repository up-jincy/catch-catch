"""DuckDB persistence and read-only query contracts."""

from customer_signal.data.database import seed_database
from customer_signal.data.source_registry import EvidenceProvider, SourceAdapter, SourceRegistry

__all__ = ["EvidenceProvider", "SourceAdapter", "SourceRegistry", "seed_database"]
