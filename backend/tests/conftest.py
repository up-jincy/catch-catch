from pathlib import Path

import pytest

from customer_signal.data.database import seed_database
from customer_signal.data.repository import DuckDBRepository
from customer_signal.domain.models import SyntheticDataset
from customer_signal.synthetic.generator import generate_dataset


@pytest.fixture
def synthetic_dataset() -> SyntheticDataset:
    return generate_dataset(seed=20260819)


@pytest.fixture
def database_path(tmp_path: Path, synthetic_dataset: SyntheticDataset) -> Path:
    path = tmp_path / "customer-signal.duckdb"
    seed_database(path, synthetic_dataset)
    return path


@pytest.fixture
def repository(database_path: Path) -> DuckDBRepository:
    return DuckDBRepository(database_path)
