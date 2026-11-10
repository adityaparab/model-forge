"""The documentation prompts keep the path and number rules a real model needs to ground."""

from modelforge_gateway.prompts import PromptRegistry

from .conftest import REPO


def test_writing_prompts_teach_paths_and_number_formats() -> None:
    registry = PromptRegistry(REPO / "prompts")
    system = registry.render(registry.get("document/system", 1))
    user = registry.render(
        registry.get("document/write_section", 1),
        section_json="{}",
        excerpts_json="{}",
        passages_json="[]",
        failures_json='{"ok": false}',
    )
    assert "tests[disc.auc.oot].status" in user and "[A:selection#selected.count]" in user
    assert "`name` for `columns`" in user
    assert "path rules above" in user  # the regeneration instruction points back to them
    assert "keeping a trailing `.0`" in system
    assert "`1.235e-05`" in system and "`0.00001235`" in system
    assert "written in words or left" in system
