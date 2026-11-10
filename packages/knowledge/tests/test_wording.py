"""Wording guard and front-matter checks over the knowledge corpus and prompts."""

from datetime import date
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_BASE = REPO_ROOT / "knowledge-base"
PROMPTS = REPO_ROOT / "prompts"

FORBIDDEN = (
    "extends SR 11-7 to LLMs",
    "extend SR 11-7 to",
    "LLM-as-judge",
    "adversarial probing",
)

REQUIRED_KEYS = {"id", "title", "source", "authority", "date", "url", "kind"}
KINDS = {"primary", "summary", "internal"}


def _files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def _corpus_files() -> list[Path]:
    return sorted(KNOWLEDGE_BASE.glob("*.md"))


def _front_matter(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{path.name} does not open with front-matter"
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    loaded = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(loaded, dict), f"{path.name} front-matter is not a mapping"
    return {str(k): v for k, v in loaded.items()}


def test_no_forbidden_wording() -> None:
    hits: list[str] = []
    for path in _files_under(KNOWLEDGE_BASE) + _files_under(PROMPTS):
        text = path.read_text(encoding="utf-8").lower()
        hits.extend(
            f"{path.relative_to(REPO_ROOT)}: {phrase!r}"
            for phrase in FORBIDDEN
            if phrase.lower() in text
        )
    assert hits == []


@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.stem)
def test_front_matter(path: Path) -> None:
    meta = _front_matter(path)
    assert REQUIRED_KEYS <= meta.keys(), f"missing keys: {REQUIRED_KEYS - meta.keys()}"
    assert meta["id"] == path.stem
    assert meta["kind"] in KINDS
    assert isinstance(meta["date"], str)
    date.fromisoformat(meta["date"])


EXPECTED_IDS = {
    "sr11-7",
    "sr26-2",
    "occ-2026-13",
    "cecl-asc-326",
    "ifrs9-ecl",
    "basel-irb",
    "modeling-standards",
    "modelforge-governance",
}


def test_corpus_is_complete() -> None:
    assert {p.stem for p in _corpus_files()} == EXPECTED_IDS
