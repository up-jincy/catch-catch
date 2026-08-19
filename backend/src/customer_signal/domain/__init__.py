"""Framework-independent domain contracts."""

from customer_signal.domain.models import CustomerEvent, EvidenceRecord, Scalar, SyntheticDataset

__all__ = ["CustomerEvent", "EvidenceRecord", "Scalar", "SyntheticDataset"]
