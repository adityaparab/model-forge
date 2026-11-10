from pathlib import Path

import pytest
from modelforge_gateway.prompts import PromptRegistry
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage.canonical import sha256_hex
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.models import PromptRow

FRONT_MATTER = (
    "---\npurpose: scope\nversion: 1\noutput_schema: ScopeProposal\ndescription: d\n---\n"
)


def write_prompt(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_registry_loads_hashes_renders_and_rejects_bad_names(tmp_path: Path) -> None:
    path = write_prompt(tmp_path, "scope/propose_plan.v1.md", FRONT_MATTER + "Hello {{ name }}")
    registry = PromptRegistry(tmp_path)

    record = registry.get("scope/propose_plan", 1)

    assert record.name == "scope/propose_plan"
    assert record.version == 1
    assert record.hash == sha256_hex(path.read_bytes())
    assert record.template == "Hello {{ name }}"
    assert record.purpose == "scope"
    assert record.output_schema == "ScopeProposal"
    assert record.description == "d"
    assert registry.get("scope/propose_plan", 1) is record
    assert registry.render(record, name="x") == "Hello x"

    for name, version in [("scope/propose_plan", 2), ("../x", 1), ("scope", 1)]:
        with pytest.raises(GatewayError) as missing:
            registry.get(name, version)
        assert missing.value.kind == "config"
    with pytest.raises(GatewayError) as undefined:
        registry.render(record)
    assert undefined.value.kind == "config"


@pytest.mark.parametrize(
    "text",
    [
        "no front matter",
        "---\npurpose: scope\n",
        "---\n- a list\n---\nbody",
        "---\npurpose: scope\nversion: 2\noutput_schema: S\ndescription: d\n---\nbody",
        "---\npurpose: nope\nversion: 1\noutput_schema: S\ndescription: d\n---\nbody",
        "---\npurpose: scope\nversion: 1\noutput_schema: ''\ndescription: d\n---\nbody",
        "---\npurpose: scope\nversion: 1\noutput_schema: S\n---\nbody",
        "---\npurpose: [scope\n---\nbody",
    ],
)
def test_registry_rejects_bad_front_matter(tmp_path: Path, text: str) -> None:
    write_prompt(tmp_path, "scope/bad.v1.md", text)
    with pytest.raises(GatewayError) as error:
        PromptRegistry(tmp_path).get("scope/bad", 1)
    assert error.value.kind == "config"
    assert "scope/bad.v1.md" in error.value.message


def test_render_reports_template_syntax_errors(tmp_path: Path) -> None:
    write_prompt(tmp_path, "scope/broken.v1.md", FRONT_MATTER + "Hello {{ name ")
    registry = PromptRegistry(tmp_path)
    record = registry.get("scope/broken", 1)
    with pytest.raises(GatewayError) as error:
        registry.render(record, name="x")
    assert error.value.kind == "config"


@pytest.mark.db
def test_registry_upserts_once_and_detects_hash_conflict(db: Database, tmp_path: Path) -> None:
    path = write_prompt(tmp_path, "scope/propose_plan.v1.md", FRONT_MATTER + "Hello {{ name }}")
    lineage = Lineage(db)
    registry = PromptRegistry(tmp_path, lineage)

    first = registry.get("scope/propose_plan", 1)
    registry.get("scope/propose_plan", 1)

    with db.connection() as conn:
        rows = conn.execute("select name, version, hash, path from prompts").fetchall()
    assert [PromptRow.model_validate(r) for r in rows] == [
        PromptRow(name="scope/propose_plan", version=1, hash=first.hash, path=first.path)
    ]

    path.write_text(FRONT_MATTER + "Goodbye {{ name }}", encoding="utf-8")
    with pytest.raises(GatewayError) as conflict:
        PromptRegistry(tmp_path, lineage).get("scope/propose_plan", 1)
    assert conflict.value.kind == "config"
