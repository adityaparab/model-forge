import pandas as pd
from modelforge_core.binning import fit_binning, transform_woe
from modelforge_core.selection import select_features


def test_selection_drops_by_iv_constant_and_correlation(synthetic: pd.DataFrame) -> None:
    frame = synthetic.assign(
        f_7=1.0,
        f_8=synthetic["f_1"].fillna(0) + 1e-3,
        f_9=synthetic["default_flag"].astype(float),
    )
    tables = fit_binning(frame, [f"f_{i}" for i in range(1, 10)])
    woe = transform_woe(frame, tables)
    sel = select_features(tables, woe, iv_min=0.02, iv_max=0.5, corr_max=0.7)
    reasons = {d.feature: d.reason for d in sel.dropped}
    assert reasons["f_7"] == "constant"
    assert reasons["f_9"] == "iv_above_max"
    # f_1 and f_8 are near-duplicates: exactly one survives,
    # the other is dropped naming the survivor
    corr_dropped = [d for d in sel.dropped if d.reason == "correlated"]
    assert [d.feature for d in corr_dropped] in (["f_1"], ["f_8"])
    survivor = "f_8" if corr_dropped[0].feature == "f_1" else "f_1"
    assert survivor in sel.selected and survivor in corr_dropped[0].detail
    assert "f_2" in sel.selected
    assert all(
        reasons.get(f) == "iv_below_min" for f in ("f_4", "f_5", "f_6") if f not in sel.selected
    )


def test_max_features_cap_records_dropped(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, [f"f_{i}" for i in range(1, 7)])
    woe = transform_woe(synthetic, tables)
    uncapped = select_features(tables, woe, iv_min=0.02, iv_max=0.5, corr_max=0.7)
    sel = select_features(tables, woe, iv_min=0.02, iv_max=0.5, corr_max=0.7, max_features=2)
    assert len(sel.selected) == 2
    assert sel.selected == uncapped.selected[:2]
    capped = {d.feature for d in sel.dropped if d.reason == "max_features"}
    assert all(f in capped for f in uncapped.selected[2:])
