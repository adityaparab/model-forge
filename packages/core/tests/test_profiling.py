import pandas as pd
from modelforge_core.profiling import profile


def test_profile_counts_and_missingness(synthetic: pd.DataFrame) -> None:
    p = profile(synthetic, dataset="synthetic")
    assert p.n_rows == 4000 and p.n_features == 6
    assert sum(p.rows_by_vintage.values()) == 4000
    assert 0.05 < p.target_rate < 0.15
    f1 = next(f for f in p.features if f.name == "f_1")
    assert 0.04 < f1.missing_share < 0.06
    assert all(abs(p.target_rate_by_vintage[v] - p.target_rate) < 0.05 for v in p.rows_by_vintage)
