import pathlib
import re

FORBIDDEN = re.compile(
    r"(?m)(?:^\s*(?:import pickle|from pickle|import cloudpickle|from cloudpickle"
    r"|import dill|from dill|from joblib import dump)|joblib\.dump)"
)
REPO = pathlib.Path(__file__).resolve().parents[3]
SCANNED = (REPO / "packages" / "core" / "modelforge_core", REPO / "examples")


def test_no_pickle_in_package() -> None:
    files = [p for root in SCANNED for p in root.rglob("*.py")]
    assert len(files) > 1
    offenders = [p for p in files if FORBIDDEN.search(p.read_text())]
    assert offenders == []
