import os
from pathlib import Path

import pandas as pd
import pytest
from modelforge_core.testing import make_synthetic_credit_frame


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def data_dir() -> Path:
    default = Path(__file__).resolve().parents[3] / "data"
    path = Path(os.environ.get("MODELFORGE_DATA_DIR", str(default)))
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def synthetic() -> pd.DataFrame:
    return make_synthetic_credit_frame()
