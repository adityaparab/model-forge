import json

import pytest
from modelforge_mcp.common import ServerSettings
from modelforge_mcp.contracts import (
    CONTRACTS_DIR,
    golden_path,
    platform_key,
    run_golden_chain,
    schema_snapshot,
    snapshot_path,
)
from modelforge_mcp.registry import TOOLS


def test_every_tool_has_a_committed_schema_snapshot_that_matches() -> None:
    for spec in TOOLS.values():
        path = snapshot_path(spec)
        assert path.exists(), (
            f"missing snapshot {path}; run `uv run modelforge-mcp contracts` "
            "and bump the version if the schema changed"
        )
        assert json.loads(path.read_text()) == schema_snapshot(spec)


def test_no_orphan_snapshots() -> None:
    expected = {snapshot_path(s) for s in TOOLS.values()}
    actual = set(CONTRACTS_DIR.glob("*/*.json"))
    assert actual == expected


def test_golden_hashes_match_for_this_platform(settings: ServerSettings) -> None:
    key = platform_key()
    if not (CONTRACTS_DIR / "goldens" / key).exists():
        pytest.skip(
            f"no goldens committed for {key}; run `make goldens` and commit "
            f"packages/mcp/contracts/goldens/{key}"
        )
    produced = run_golden_chain(settings)
    for (server, tool), refs in produced.items():
        spec = TOOLS[(server, tool)]
        assert golden_path(spec, key).read_text().split() == [r.id for r in refs], (
            f"{server}/{tool}@{spec.version} output changed without a version bump"
        )
