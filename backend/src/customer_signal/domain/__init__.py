"""Framework-independent domain contracts."""

from customer_signal.domain.models import (
    CanonicalCustomerEvent,
    CustomerEvent,
    EvidenceRecord,
    Scalar,
    SyntheticDataset,
)
from customer_signal.domain.types import GenericPrimitiveName, PrimitiveName, SourceId

__all__ = [
    "CanonicalCustomerEvent",
    "CustomerEvent",
    "EvidenceRecord",
    "GenericPrimitiveName",
    "PrimitiveName",
    "Scalar",
    "SourceId",
    "SyntheticDataset",
]
