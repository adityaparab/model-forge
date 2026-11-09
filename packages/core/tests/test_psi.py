import numpy as np
import pandas as pd
import pytest
from modelforge_core.psi import compute_psi, psi, psi_band, psi_from_shares
from modelforge_core.schemas import Thresholds


def test_psi_from_shares_golden() -> None:
    assert psi_from_shares(np.array([0.5, 0.5]), np.array([0.6, 0.4])) == pytest.approx(
        0.040547, abs=1e-6
    )
    assert psi_from_shares(np.array([0.5, 0.5]), np.array([0.5, 0.5])) == 0.0


def test_psi_zero_for_same_distribution_and_positive_for_shift() -> None:
    rng = np.random.default_rng(0)
    base = rng.normal(size=20000)
    assert psi(base, rng.normal(size=20000)) < 0.01
    assert psi(base, rng.normal(loc=1.0, size=20000)) > 0.25


def test_psi_counts_missing_as_bucket() -> None:
    base = np.array([1.0, 2.0, 3.0, 4.0] * 100)
    shifted = np.array([1.0, 2.0, np.nan, np.nan] * 100)
    assert psi(base, shifted) > 0.1


def test_psi_band_thresholds() -> None:
    t = Thresholds()
    assert (psi_band(0.05, t), psi_band(0.10, t), psi_band(0.25, t)) == ("pass", "warn", "fail")


def test_compute_psi_report_shape(synthetic: pd.DataFrame) -> None:
    report = compute_psi(synthetic, base_vintages=[1, 2, 3, 4], compare_vintages=[5])
    assert [e.feature for e in report.entries] == [f"f_{i}" for i in range(1, 7)]
    assert all(e.band == "pass" for e in report.entries)
