import re
from pathlib import Path

FORBIDDEN = re.compile(
    r"(?m)(?:^\s*(?:import pickle|from pickle|import cloudpickle|from cloudpickle"
    r"|import dill|from dill|from joblib import dump)|joblib\.dump)"
)


def test_no_pickle_in_any_package() -> None:
    packages = Path(__file__).resolve().parents[3] / "packages"
    roots = sorted(p for p in packages.glob("*/modelforge_*") if p.is_dir())
    assert len(roots) >= 4
    examples = packages.parent / "examples"
    assert examples.is_dir()
    scanned = [*roots, examples]
    offenders = [
        p for root in scanned for p in root.rglob("*.py") if FORBIDDEN.search(p.read_text())
    ]
    assert offenders == []
