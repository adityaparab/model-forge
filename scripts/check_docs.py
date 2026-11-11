"""Offline documentation checks for the ModelForge repository.

Usage: uv run python scripts/check_docs.py [--root DIR] [--no-mermaid] [--patterns FILE]
       [--strict-readme]

Checks: relative links and anchors in README.md and docs/**/*.md, media file sizes,
employer-neutrality patterns over every tracked file, the README length (with
--strict-readme), README results blocks, and Mermaid blocks rendered with the pinned mmdc.
Exits 0 when every check passes, otherwise 1 with one
``check: <name>: <path>: <reason>`` line per failure. Pure standard library.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
DEFAULT_MMDC = SCRIPTS_DIR / "node_modules" / ".bin" / "mmdc"

MIB = 1024 * 1024
SIZE_LIMITS: dict[str, int] = {
    "docs/media/demo.gif": 8 * MIB,
    "docs/media/demo.mp4": 25 * MIB,
}

ENV_PATTERNS = "MODELFORGE_NEUTRALITY_PATTERNS"
PATTERNS_FILE = ".neutrality-patterns"

# The generic patterns are assembled from pieces so this file never matches them itself.
HOME_USERS = r"/Users/[A-Za-z0-9._-]+" + "/"
HOME_LINUX = r"/home/[A-Za-z0-9._-]+" + "/"
WORKTREES = r"\.claude/worktree" + "s/"
DB_CREDENTIALS = r"postgresql" + r"://[^\s]+:[^\s]+@"
GENERIC_PATTERNS = (HOME_USERS, HOME_LINUX, WORKTREES, DB_CREDENTIALS)

FIXTURES_GLOB = "scripts/tests/fixtures/*"
# Files that define these checks and therefore quote the patterns and their examples.
PATTERN_DEFINITION_DOCS = (
    "docs/superpowers/specs/2026-11-11-sp6-docs-presentation-design.md",
    "docs/superpowers/plans/2026-11-11-sp6-docs-presentation.md",
)
DB_CREDENTIALS_ALLOWLIST = (
    "docker-compose.yml",
    ".env.example",
    "README.md",
    "docs/reference/configuration.md",
    "docs/reference/compose.md",
    "docs/reference/run.md",
    "packages/orchestrator/modelforge_orchestrator/config.py",
    ".github/workflows/*.yml",
    FIXTURES_GLOB,
    # The development default URL as used by test helpers, tests and the build record.
    "packages/lineage/modelforge_lineage/testing.py",
    "packages/*/tests/*",
    "docs/superpowers/*",
)
PATTERN_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    HOME_USERS: (FIXTURES_GLOB, *PATTERN_DEFINITION_DOCS),
    HOME_LINUX: (FIXTURES_GLOB, *PATTERN_DEFINITION_DOCS),
    WORKTREES: (FIXTURES_GLOB, *PATTERN_DEFINITION_DOCS),
    DB_CREDENTIALS: DB_CREDENTIALS_ALLOWLIST,
}
PRIVATE_ALLOWLIST = (FIXTURES_GLOB,)

CHROMIUM_MISSING = "chromium not found; run npx --prefix scripts playwright install chromium"
MMDC_MISSING = "mmdc not installed (run make scripts-install)"

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w-]*)")
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]*)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
HTML_ANCHOR_RE = re.compile(r"<a\s+[^>]*(?:name|id)=\"([^\"]+)\"", re.IGNORECASE)
RESULTS_OPEN_RE = re.compile(r"<!--\s*results:([\w.-]+)\s*-->")
RESULTS_CLOSE_RE = re.compile(r"<!--\s*/results:([\w.-]+)\s*-->")
TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")


@dataclass(frozen=True)
class Link:
    path: str
    anchor: str
    line: int


@dataclass(frozen=True)
class Failure:
    check: str
    path: str
    reason: str

    def __str__(self) -> str:
        return f"check: {self.check}: {self.path}: {self.reason}"


def tracked_files(root: Path) -> list[str]:
    """Paths tracked by git in ``root``, relative and with forward slashes."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode()
    return [p for p in out.split("\0") if p]


def markdown_files(tracked: list[str]) -> list[str]:
    return [p for p in tracked if p == "README.md" or (p.startswith("docs/") and p.endswith(".md"))]


def _prose_lines(md_text: str, strip_code: bool = True) -> list[tuple[int, str]]:
    """(line number, text) for lines outside fenced code, inline code spans removed by default."""
    lines: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(md_text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                continue
            lines.append((number, INLINE_CODE_RE.sub("", line) if strip_code else line))
        elif match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            fence = None
    return lines


def find_links(md_text: str) -> list[Link]:
    """Relative links and images; external schemes (http, https, mailto, ...) are skipped."""
    links: list[Link] = []
    for number, line in _prose_lines(md_text):
        for match in LINK_RE.finditer(line):
            target = match.group(1)
            if not target or SCHEME_RE.match(target):
                continue
            path, _, anchor = target.partition("#")
            links.append(Link(urllib.parse.unquote(path), anchor, number))
    return links


def slugify(heading: str) -> str:
    """GitHub's heading slug: lowercase, punctuation other than - and _ dropped, spaces to -."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def heading_slugs(md_text: str) -> set[str]:
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    for _, line in _prose_lines(md_text, strip_code=False):
        slugs.update(HTML_ANCHOR_RE.findall(line))
        match = HEADING_RE.match(line)
        if not match:
            continue
        slug = slugify(match.group(1))
        seen = counts.get(slug, 0)
        counts[slug] = seen + 1
        slugs.add(slug if seen == 0 else f"{slug}-{seen}")
    return slugs


def check_links(root: Path) -> list[Failure]:
    tracked = tracked_files(root)
    tracked_set = set(tracked)
    tracked_dirs = {str(Path(p).parent) for p in tracked}
    failures: list[Failure] = []
    slug_cache: dict[str, set[str]] = {}

    def slugs_of(rel: str) -> set[str]:
        if rel not in slug_cache:
            slug_cache[rel] = heading_slugs((root / rel).read_text(encoding="utf-8"))
        return slug_cache[rel]

    for source in markdown_files(tracked):
        source_path = root / source
        if not source_path.is_file():
            continue
        for link in find_links(source_path.read_text(encoding="utf-8")):
            written = link.path + (f"#{link.anchor}" if link.anchor else "")
            if link.path:
                target = os.path.normpath(os.path.join(os.path.dirname(source), link.path))
                target = target.replace(os.sep, "/")
            else:
                target = source
            if target.startswith("../") or target == "..":
                failures.append(
                    Failure("links", source, f"line {link.line}: {written} leaves the repository")
                )
                continue
            is_file = target in tracked_set and (root / target).is_file()
            is_dir = target in tracked_dirs or target == "."
            if not (is_file or is_dir):
                failures.append(
                    Failure("links", source, f"line {link.line}: {link.path} not found")
                )
                continue
            if link.anchor and is_file and target.endswith(".md"):
                if link.anchor.lower() not in slugs_of(target):
                    reason = f"line {link.line}: {link.path or source} has no heading"
                    failures.append(Failure("links", source, f"{reason} #{link.anchor}"))
    return failures


def check_sizes(root: Path, limits: dict[str, int]) -> list[Failure]:
    failures: list[Failure] = []
    for rel, limit in limits.items():
        path = root / rel
        if path.is_file() and (size := path.stat().st_size) >= limit:
            failures.append(
                Failure("sizes", rel, f"{size} bytes, must be under {limit} ({limit / MIB:g} MiB)")
            )
    return failures


def load_patterns(root: Path, patterns_file: Path | None = None) -> list[re.Pattern[str]]:
    """Generic patterns, then lines of ``.neutrality-patterns``, then the environment list."""
    patterns = [re.compile(p) for p in GENERIC_PATTERNS]
    private: list[str] = []
    file = patterns_file if patterns_file is not None else root / PATTERNS_FILE
    if file.is_file():
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                private.append(line)
    private.extend(p.strip() for p in os.environ.get(ENV_PATTERNS, "").split(";") if p.strip())
    patterns.extend(re.compile(p, re.IGNORECASE) for p in private)
    return patterns


def _allowed(rel: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(rel, glob) for glob in globs)


def check_neutrality(root: Path, patterns: list[re.Pattern[str]]) -> list[Failure]:
    failures: list[Failure] = []
    for rel in tracked_files(root):
        path = root / rel
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        active = [
            p
            for p in patterns
            if not _allowed(rel, PATTERN_ALLOWLISTS.get(p.pattern, PRIVATE_ALLOWLIST))
        ]
        if not active:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            for pattern in active:
                if pattern.search(line):
                    failures.append(
                        Failure("neutrality", rel, f"{rel}:{number} matches '{pattern.pattern}'")
                    )
    return failures


def check_readme_length(root: Path, max_lines: int = 220) -> list[Failure]:
    readme = root / "README.md"
    if not readme.is_file():
        return []
    count = len(readme.read_text(encoding="utf-8").splitlines())
    if count > max_lines:
        return [Failure("readme-length", "README.md", f"{count} lines, limit {max_lines}")]
    return []


def _has_data_row(body: list[str]) -> bool:
    table = [line.strip() for line in body if line.strip().startswith("|")]
    for index, line in enumerate(table):
        if TABLE_SEPARATOR_RE.match(line):
            return index > 0 and len(table) > index + 1
    return False


def check_results_blocks(root: Path) -> list[Failure]:
    readme = root / "README.md"
    if not readme.is_file():
        return []
    lines = readme.read_text(encoding="utf-8").splitlines()
    failures: list[Failure] = []
    open_name: str | None = None
    body: list[str] = []
    for line in lines:
        opened = RESULTS_OPEN_RE.search(line)
        closed = RESULTS_CLOSE_RE.search(line)
        if opened:
            if open_name is not None:
                failures.append(
                    Failure("results-blocks", "README.md", f"block '{open_name}' is not closed")
                )
            open_name, body = opened.group(1), []
        elif closed:
            name = closed.group(1)
            if open_name != name:
                reason = f"closing marker for '{name}' has no opening"
                failures.append(Failure("results-blocks", "README.md", reason))
            elif not _has_data_row(body):
                reason = f"block '{name}' has no table data row"
                failures.append(Failure("results-blocks", "README.md", reason))
            open_name = None
        elif open_name is not None:
            body.append(line)
    if open_name is not None:
        failures.append(
            Failure("results-blocks", "README.md", f"block '{open_name}' is not closed")
        )
    return failures


def mermaid_blocks(md_text: str) -> list[tuple[int, str]]:
    """(fence line number, source) for every ```mermaid block."""
    blocks: list[tuple[int, str]] = []
    fence: str | None = None
    start = 0
    body: list[str] = []
    is_mermaid = False
    for number, line in enumerate(md_text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence, start, body = match.group(1), number, []
                is_mermaid = match.group(2) == "mermaid"
        elif match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            if is_mermaid:
                blocks.append((start, "\n".join(body) + "\n"))
            fence = None
        else:
            body.append(line)
    return blocks


def resolve_chromium() -> str | None:
    """The Playwright Chromium executable from scripts/node_modules, if it is installed."""
    script = "console.log(require('playwright').chromium.executablePath())"
    try:
        out = subprocess.run(
            ["node", "-e", script],
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and Path(out).exists() else None


def _render_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in lines:
        if "error" in line.lower():
            return line[:200]
    return lines[-1][:200] if lines else "no output"


def check_mermaid(root: Path, mmdc: Path = DEFAULT_MMDC) -> list[Failure]:
    sources: list[tuple[str, list[tuple[int, str]]]] = []
    for rel in markdown_files(tracked_files(root)):
        path = root / rel
        if path.is_file() and (blocks := mermaid_blocks(path.read_text(encoding="utf-8"))):
            sources.append((rel, blocks))
    if not sources:
        return []
    if not mmdc.is_file():
        return [Failure("mermaid", rel, MMDC_MISSING) for rel, _ in sources]
    env = dict(os.environ)
    if not env.get("PUPPETEER_EXECUTABLE_PATH"):
        chromium = resolve_chromium()
        if chromium is None:
            return [Failure("mermaid", rel, CHROMIUM_MISSING) for rel, _ in sources]
        env["PUPPETEER_EXECUTABLE_PATH"] = chromium
    failures: list[Failure] = []
    with tempfile.TemporaryDirectory(prefix="check-docs-mermaid-") as tmp:
        config = Path(tmp) / "puppeteer.json"
        config.write_text(json.dumps({"args": ["--no-sandbox"]}))
        for rel, blocks in sources:
            for index, (line, source) in enumerate(blocks):
                diagram = Path(tmp) / f"block-{len(failures)}-{index}.mmd"
                diagram.write_text(source, encoding="utf-8")
                command = [str(mmdc), "-i", str(diagram), "-o", str(diagram.with_suffix(".svg"))]
                command += ["-q", "-p", str(config)]
                try:
                    result = subprocess.run(
                        command, env=env, capture_output=True, text=True, timeout=120
                    )
                except subprocess.TimeoutExpired:
                    failures.append(Failure("mermaid", rel, f"block at line {line} timed out"))
                    continue
                if result.returncode != 0 or not diagram.with_suffix(".svg").is_file():
                    reason = (
                        f"block at line {line} failed to render: {_render_error(result.stderr)}"
                    )
                    failures.append(Failure("mermaid", rel, reason))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline documentation checks.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--no-mermaid", action="store_true", help="skip the Mermaid render check")
    parser.add_argument(
        "--patterns", type=Path, help=f"private patterns file (default {PATTERNS_FILE})"
    )
    parser.add_argument(
        "--strict-readme", action="store_true", help="enforce the 220-line README limit"
    )
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    try:
        tracked_files(root)
    except (OSError, subprocess.CalledProcessError):
        print(f"check: git: {root}: not a git repository")
        return 1
    failures = check_links(root)
    failures += check_sizes(root, SIZE_LIMITS)
    failures += check_neutrality(root, load_patterns(root, args.patterns))
    if args.strict_readme:
        failures += check_readme_length(root)
    failures += check_results_blocks(root)
    if not args.no_mermaid:
        failures += check_mermaid(root)
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print("docs-check: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
