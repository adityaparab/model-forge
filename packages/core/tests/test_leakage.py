import numpy as np
import pandas as pd
from modelforge_core.leakage import check_leakage


def test_leakage_flags_target_copy_constant_and_identifier(synthetic: pd.DataFrame) -> None:
    frame = synthetic.copy()
    frame["f_7"] = frame["default_flag"].astype(float)  # perfect separation
    frame["f_8"] = 1.0  # constant
    frame["f_9"] = np.arange(len(frame), dtype=float)  # identifier-like
    report = check_leakage(frame, dataset="synthetic")
    kinds = {(f.feature, f.kind) for f in report.flags}
    assert ("f_7", "perfect_separation") in kinds
    assert ("f_8", "constant") in kinds
    assert ("f_9", "identifier_like") in kinds
    assert not any(f.feature in {"f_1", "f_2", "f_3"} for f in report.flags)
    assert report.duplicate_rows == 0


def test_leakage_high_auc_flag_threshold(synthetic: pd.DataFrame) -> None:
    frame = synthetic.copy()
    noise = np.random.default_rng(1).normal(scale=0.5, size=len(frame))
    frame["f_7"] = frame["default_flag"] + noise
    report = check_leakage(frame, dataset="synthetic", auc_threshold=0.9)
    assert any(
        f.feature == "f_7" and f.kind == "high_single_auc" and f.value is not None and f.value > 0.9
        for f in report.flags
    )
