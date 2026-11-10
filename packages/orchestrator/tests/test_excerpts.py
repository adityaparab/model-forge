from pathlib import Path

import pytest
import yaml
from modelforge_lineage.citations import resolve_path
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import SectionSpec
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.excerpts import ALIAS_DESCRIPTIONS, KIND_TO_REF, excerpt, for_section
from modelforge_orchestrator.schemas import RunState

SECTIONS = Path(__file__).resolve().parents[3] / "templates/sr11-7-dev-doc/sections.yaml"


def _section(section_id: str) -> SectionSpec:
    specs = [SectionSpec.model_validate(s) for s in yaml.safe_load(SECTIONS.read_text())]
    return next(s for s in specs if s.id == section_id)


def test_excerpt_projections() -> None:
    assert excerpt(
        "split_manifest",
        {"row_ids": {"train": ["a", "b"], "oot": ["c"]}, "digests": {"train": "x"}},
    ) == {"row_ids": {"train": {"count": 2}, "oot": {"count": 1}}, "digests": {"train": "x"}}
    assert "model_text" not in excerpt("gbm_model", {"model_text": "tree", "num_trees": 3})
    assert excerpt(
        "scorecard", {"features": [{"feature": "f_1", "coefficient": -1.0, "bins": [1, 2]}]}
    )["features"][0] == {"feature": "f_1", "coefficient": -1.0, "bins": {"count": 2}}
    assert excerpt(
        "column_dictionary",
        {"dataset": "d", "columns": [{"name": "f_1", "original": "Attr1", "description": "ratio"}]},
    )["columns"] == [{"name": "f_1", "description": "ratio"}]
    assert excerpt(
        "calibration_map", {"method": "isotonic", "isotonic_x": [0.1, 0.2], "isotonic_y": [0, 1]}
    )["isotonic_x"] == {"count": 2}
    assert excerpt("profile", {"n_rows": 5}) == {"n_rows": 5}


def test_excerpt_does_not_mutate_its_input() -> None:
    data = {"features": [{"feature": "f_1", "bins": [1, 2]}]}
    excerpt("binning_tables", data)
    assert data == {"features": [{"feature": "f_1", "bins": [1, 2]}]}


def test_excerpt_paths_still_resolve() -> None:
    data = {"row_ids": {"train": ["a", "b"], "oot": ["c"]}, "digests": {"train": "x"}}
    assert resolve_path(excerpt("split_manifest", data), "row_ids.train.count") == 2
    assert resolve_path(data, "row_ids.train.count") == 2


def test_every_alias_has_a_description() -> None:
    assert set(ALIAS_DESCRIPTIONS) == set(KIND_TO_REF.values())
    assert "manifest" not in KIND_TO_REF


@pytest.mark.db
def test_for_section_selects_aliases_and_reports_unavailable(
    validated_state: RunState, deps: Deps
) -> None:
    assert validated_state.plan is not None
    plan_ref: ArtifactRef = deps.store.put_model(validated_state.plan, kind="modeling_plan")

    performance = for_section(_section("performance"), validated_state, deps.store, plan_ref)
    assert set(performance.aliases) >= {"validation", "discrimination", "benchmark"}
    assert isinstance(performance.excerpts["discrimination"]["by_split"], list)

    no_psi = validated_state.model_copy(
        update={"refs": {k: v for k, v in validated_state.refs.items() if k != "psi"}}
    )
    data = for_section(_section("data"), no_psi, deps.store, plan_ref)
    assert "psi" in data.unavailable
    assert "psi" not in data.aliases
    assert isinstance(data.excerpts["split"]["row_ids"]["train"], dict)

    governance = for_section(_section("governance"), validated_state, deps.store, plan_ref)
    assert set(governance.aliases) == {"plan"}
    assert governance.unavailable == ["manifest"]

    summary = for_section(_section("executive_summary"), validated_state, deps.store, plan_ref)
    assert set(summary.aliases) >= {"plan", "profile", "discrimination"}

    methodology = for_section(_section("methodology"), validated_state, deps.store, plan_ref)
    assert methodology.aliases["plan"] == plan_ref
