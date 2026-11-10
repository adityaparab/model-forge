import pytest
from modelforge_lineage.citations import PathError, format_number, parse_path, resolve_path

DOC = {
    "by_split": [{"split": "oot", "auc": 0.874}, {"split": "val", "auc": 0.9}],
    "tests": [{"test_id": "disc.auc.oot", "value": 0.874, "status": "pass"}],
    "features": [{"feature": "f_1", "iv": 0.3, "bins": [1, 2, 3]}],
    "grades": [{"grade": 1, "p_value": 0.5}],
    "columns": [{"name": "f_1", "description": "ratio"}],
    "selected": ["f_1", "f_2"],
    "n_rows": 43405,
}


def test_parse_path_splits_and_keeps_bracketed_steps() -> None:
    assert parse_path("tests[disc.auc.oot].value") == ["tests", "disc.auc.oot", "value"]
    assert parse_path("by_split.oot.auc") == ["by_split", "oot", "auc"]
    with pytest.raises(PathError):
        parse_path("a..b")


@pytest.mark.parametrize("path", ["", "a.", ".a", "a[b", "a[]"])
def test_parse_path_rejects_malformed(path: str) -> None:
    with pytest.raises(PathError):
        parse_path(path)


def test_path_error_is_key_error() -> None:
    assert issubclass(PathError, KeyError)


def test_resolve_path_grammar() -> None:
    assert resolve_path(DOC, "by_split.oot.auc") == 0.874
    assert resolve_path(DOC, "tests[disc.auc.oot].status") == "pass"
    assert resolve_path(DOC, "features.f_1.iv") == 0.3
    assert resolve_path(DOC, "features.f_1.bins.count") == 3
    assert resolve_path(DOC, "grades.1.p_value") == 0.5
    assert resolve_path(DOC, "columns.f_1.description") == "ratio"
    assert resolve_path(DOC, "selected.count") == 2
    assert resolve_path(DOC, "selected") == ["f_1", "f_2"]
    assert resolve_path(DOC, "n_rows") == 43405


@pytest.mark.parametrize(
    "path",
    ["by_split.train.auc", "missing", "n_rows.x", "selected.f_1", "tests.disc.auc.oot.value"],
)
def test_resolve_path_failures(path: str) -> None:
    with pytest.raises(PathError):
        resolve_path(DOC, path)


def test_format_number() -> None:
    values = (0.812345, 43405, float("nan"), None, "NaN", True)
    assert [format_number(v) for v in values] == ["0.8123", "43405", "n/a", "n/a", "n/a", "n/a"]
