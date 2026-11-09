import sys
from pathlib import Path

import pytest
from modelforge_core.schemas import ValidationReport
from modelforge_core.scorecard import from_json

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples"))

from core_pipeline import run_pipeline  # noqa: E402


@pytest.mark.slow
def test_polish_end_to_end(data_dir: Path, tmp_path: Path) -> None:
    result = run_pipeline(
        dataset="polish_bankruptcy",
        oot_vintages=[5],
        data_dir=data_dir,
        out_dir=tmp_path,
        seed=42,
        max_features=20,
    )
    report: ValidationReport = result.report
    auc_oot = report.get("disc.auc.oot").value
    assert auc_oot is not None and auc_oot > 0.65
    assert len(report.tests) == 13 and report.get("sens.signs").status == "pass"
    written = {p.name for p in (tmp_path / "polish_bankruptcy").iterdir()}
    assert written == {
        "scorecard.json",
        "gbm_model.txt",
        "validation_report.json",
        "binning_tables.json",
        "column_dictionary.json",
        "profile.json",
    }
    scorecard_text = (tmp_path / "polish_bankruptcy" / "scorecard.json").read_text()
    assert from_json(scorecard_text).model_name == "polish_bankruptcy_pd"
    assert 5 <= len(result.selection.selected) <= 20
    assert set(result.selection.selected).isdisjoint(d.feature for d in result.selection.dropped)
    in_model = {f.feature for f in result.scorecard.features}
    assert all(
        d.feature not in in_model for d in result.selection.dropped if d.reason == "wrong_sign"
    )


@pytest.mark.slow
def test_taiwan_end_to_end_marks_oot_not_applicable(data_dir: Path, tmp_path: Path) -> None:
    result = run_pipeline(
        dataset="taiwan_bankruptcy",
        oot_vintages=[],
        data_dir=data_dir,
        out_dir=tmp_path,
        seed=42,
        max_features=20,
    )
    assert result.report.get("disc.auc.oot").status == "not_applicable"
    auc_val = result.report.get("disc.auc.val").value
    assert auc_val is not None and auc_val > 0.80
