import pathlib
import re

FORBIDDEN = re.compile(
    r"^\s*(import pickle|from pickle|import cloudpickle|import dill"
    r"|from joblib import dump|joblib\.dump)",
    re.M,
)


def test_no_pickle_in_package() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "modelforge_core"
    offenders = [p for p in root.rglob("*.py") if FORBIDDEN.search(p.read_text())]
    assert offenders == []
