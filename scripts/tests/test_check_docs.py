"""Tests for scripts/check_docs.py, run against a temporary git repository built from fixtures."""

import shutil
import subprocess
from pathlib import Path

import check_docs
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MMDC = Path(__file__).resolve().parents[1] / "node_modules" / ".bin" / "mmdc"
# Built from pieces so this tracked file never matches the generic neutrality patterns itself.
HOME_PATH = "/" + "Users" + "/someone/x"


def _git_add(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)


@pytest.fixture
def tmp_docs_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("MODELFORGE_NEUTRALITY_PATTERNS", raising=False)
    root = tmp_path / "docs_ok"
    shutil.copytree(FIXTURES / "docs_ok", root)
    _git_add(root)
    return root


def _append_readme(root: Path, text: str) -> None:
    readme = root / "README.md"
    readme.write_text(readme.read_text() + text)
    _git_add(root)


def test_ok_tree_passes(tmp_docs_ok: Path) -> None:
    assert check_docs.main(["--root", str(tmp_docs_ok), "--no-mermaid"]) == 0


def test_ok_tree_has_no_failures_per_check(tmp_docs_ok: Path) -> None:
    assert check_docs.check_links(tmp_docs_ok) == []
    assert check_docs.check_sizes(tmp_docs_ok, check_docs.SIZE_LIMITS) == []
    assert check_docs.check_neutrality(tmp_docs_ok, check_docs.load_patterns(tmp_docs_ok)) == []
    assert check_docs.check_readme_length(tmp_docs_ok) == []
    assert check_docs.check_results_blocks(tmp_docs_ok) == []


def test_find_links() -> None:
    text = "intro\n[a](docs/a.md) and ![b](img.png)\n[c](x.md#sec) [d](#top) [e](https://x.org)\n"
    links = check_docs.find_links(text)
    assert links == [
        check_docs.Link("docs/a.md", "", 2),
        check_docs.Link("img.png", "", 2),
        check_docs.Link("x.md", "sec", 3),
        check_docs.Link("", "top", 3),
    ]


def test_find_links_ignores_code() -> None:
    text = "```md\n[a](missing.md)\n```\nsee `[b](missing.md)`\n"
    assert check_docs.find_links(text) == []


def test_slug_follows_github_rule() -> None:
    assert check_docs.slugify("Section two: setup") == "section-two-setup"
    assert check_docs.slugify("The `docs` job (CI)") == "the-docs-job-ci"
    assert check_docs.slugify("snake_case and dash-ed") == "snake_case-and-dash-ed"


def test_broken_link_fails(tmp_docs_ok: Path) -> None:
    _append_readme(tmp_docs_ok, "\nSee [missing](docs/missing.md).\n")
    failures = check_docs.check_links(tmp_docs_ok)
    assert failures[0].check == "links" and "docs/missing.md" in failures[0].reason


def test_untracked_link_target_fails(tmp_docs_ok: Path) -> None:
    (tmp_docs_ok / "docs" / "untracked.md").write_text("# Untracked\n")
    readme = tmp_docs_ok / "README.md"
    readme.write_text(readme.read_text() + "\n[u](docs/untracked.md)\n")
    subprocess.run(["git", "-C", str(tmp_docs_ok), "add", "README.md"], check=True)
    assert any("docs/untracked.md" in f.reason for f in check_docs.check_links(tmp_docs_ok))


def test_missing_anchor_fails(tmp_docs_ok: Path) -> None:
    _append_readme(tmp_docs_ok, "\n[x](docs/a.md#not-a-heading)\n")
    assert any(f.reason.endswith("#not-a-heading") for f in check_docs.check_links(tmp_docs_ok))


def test_oversized_gif_fails(tmp_docs_ok: Path) -> None:
    (tmp_docs_ok / "docs" / "media" / "demo.gif").write_bytes(b"\0" * (8 * 1024 * 1024 + 1))
    failures = check_docs.check_sizes(tmp_docs_ok, {"docs/media/demo.gif": 8 * 1024 * 1024})
    assert failures[0].check == "sizes"


def test_gif_at_the_limit_passes(tmp_docs_ok: Path) -> None:
    (tmp_docs_ok / "docs" / "media" / "demo.gif").write_bytes(b"\0" * (8 * 1024 * 1024))
    assert check_docs.check_sizes(tmp_docs_ok, {"docs/media/demo.gif": 8 * 1024 * 1024}) == []


def test_neutrality_hit_fails(tmp_docs_ok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODELFORGE_NEUTRALITY_PATTERNS", "Acme Bank;zephyr-box")
    (tmp_docs_ok / "docs" / "a.md").write_text("built at Acme Bank\n")
    failures = check_docs.check_neutrality(tmp_docs_ok, check_docs.load_patterns(tmp_docs_ok))
    assert failures[0].reason == "docs/a.md:1 matches 'Acme Bank'"


def test_neutrality_patterns_file_is_read(tmp_docs_ok: Path) -> None:
    (tmp_docs_ok / ".neutrality-patterns").write_text("# private\n\nzephyr-box\n")
    (tmp_docs_ok / "docs" / "a.md").write_text("ran on zephyr-box\n")
    failures = check_docs.check_neutrality(tmp_docs_ok, check_docs.load_patterns(tmp_docs_ok))
    assert [f.reason for f in failures] == ["docs/a.md:1 matches 'zephyr-box'"]


def test_generic_home_path_fails(tmp_docs_ok: Path) -> None:
    (tmp_docs_ok / "docs" / "a.md").write_text(f"# Guide\n\nlog at {HOME_PATH}\n")
    failures = check_docs.check_neutrality(tmp_docs_ok, check_docs.load_patterns(tmp_docs_ok))
    assert failures[0].check == "neutrality" and failures[0].reason.startswith("docs/a.md:3 ")


def test_db_credentials_allowed_only_in_allowlist(tmp_docs_ok: Path) -> None:
    url = "postgresql" + "://user:pw@localhost/db\n"
    (tmp_docs_ok / "docker-compose.yml").write_text(url)
    (tmp_docs_ok / "docs" / "a.md").write_text(url)
    _git_add(tmp_docs_ok)
    failures = check_docs.check_neutrality(tmp_docs_ok, check_docs.load_patterns(tmp_docs_ok))
    assert [f.path for f in failures] == ["docs/a.md"]


def test_readme_over_220_lines_fails(tmp_docs_ok: Path) -> None:
    _append_readme(tmp_docs_ok, "line\n" * 220)
    failures = check_docs.check_readme_length(tmp_docs_ok, max_lines=220)
    assert failures[0].check == "readme-length" and failures[0].path == "README.md"


def test_readme_length_only_enforced_with_strict_flag(tmp_docs_ok: Path) -> None:
    _append_readme(tmp_docs_ok, "line\n" * 220)
    assert check_docs.main(["--root", str(tmp_docs_ok), "--no-mermaid"]) == 0
    argv = ["--root", str(tmp_docs_ok), "--no-mermaid", "--strict-readme"]
    assert check_docs.main(argv) == 1


def test_empty_results_block_fails(tmp_docs_ok: Path) -> None:
    _append_readme(
        tmp_docs_ok, "\n<!-- results:empty -->\n| a | b |\n|---|---|\n<!-- /results:empty -->\n"
    )
    failures = check_docs.check_results_blocks(tmp_docs_ok)
    assert [(f.check, f.reason) for f in failures] == [
        ("results-blocks", "block 'empty' has no table data row")
    ]


def test_unclosed_results_block_fails(tmp_docs_ok: Path) -> None:
    _append_readme(tmp_docs_ok, "\n<!-- results:open -->\n")
    assert check_docs.check_results_blocks(tmp_docs_ok)[0].reason == "block 'open' is not closed"


def test_main_prints_one_line_per_failure(
    tmp_docs_ok: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _append_readme(tmp_docs_ok, "\n[m](docs/missing.md)\n")
    assert check_docs.main(["--root", str(tmp_docs_ok), "--no-mermaid"]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["check: links: README.md: line 27: docs/missing.md not found"]


def test_mermaid_check_fails_when_mmdc_missing(tmp_docs_ok: Path) -> None:
    failures = check_docs.check_mermaid(tmp_docs_ok, mmdc=tmp_docs_ok / "nope")
    assert failures[0].reason.startswith("mmdc not installed")


def test_mermaid_check_fails_when_browser_missing(
    tmp_docs_ok: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PUPPETEER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(check_docs, "resolve_chromium", lambda: None)
    fake_mmdc = tmp_docs_ok / "mmdc"
    fake_mmdc.write_text("#!/bin/sh\nexit 0\n")
    failures = check_docs.check_mermaid(tmp_docs_ok, mmdc=fake_mmdc)
    assert failures[0].reason == (
        "chromium not found; run npx --prefix scripts playwright install chromium"
    )


def test_mermaid_check_skips_tree_without_blocks(tmp_docs_ok: Path) -> None:
    readme = tmp_docs_ok / "README.md"
    readme.write_text(readme.read_text().split("## Diagram")[0])
    assert check_docs.check_mermaid(tmp_docs_ok, mmdc=tmp_docs_ok / "nope") == []


needs_mmdc = pytest.mark.skipif(not MMDC.exists(), reason="scripts/node_modules not installed")


@needs_mmdc
def test_resolve_chromium_finds_playwright_browser() -> None:
    path = check_docs.resolve_chromium()
    assert path is not None and Path(path).exists()


@needs_mmdc
def test_mermaid_block_renders(tmp_docs_ok: Path) -> None:
    assert check_docs.check_mermaid(tmp_docs_ok, mmdc=MMDC) == []


@needs_mmdc
def test_bad_mermaid_block_fails(tmp_docs_ok: Path) -> None:
    _append_readme(tmp_docs_ok, "\n```mermaid\nflowchart LR\n  a -->\n```\n")
    failures = check_docs.check_mermaid(tmp_docs_ok, mmdc=MMDC)
    assert len(failures) == 1
    assert failures[0].check == "mermaid" and failures[0].path == "README.md"
    assert failures[0].reason.startswith("block at line 27 failed to render")
