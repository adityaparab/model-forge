"""The evals configuration model and the committed ``evals/evals.yml``."""

from pathlib import Path

import pytest
from modelforge_evals.config import EvalConfigError, load_config

REPO = Path(__file__).resolve().parents[3]


def test_committed_config_has_the_four_profiles() -> None:
    config = load_config(REPO / "evals/evals.yml")
    assert set(config.profiles) == {"fake", "default", "ci-cheap", "local-ollama"}
    fake = config.profiles["fake"]
    assert fake.compare == "exact"
    assert fake.brief == "synthetic"
    assert "wall_time_by_node_s.*" in fake.ignore
    assert config.profiles["default"].compare == "tolerance"
    assert config.profiles["default"].budgets == {
        "cost_usd": 10.0,
        "probe_cost_usd": 4.0,
        "wall_time_s": 3600,
    }
    assert config.profiles["ci-cheap"].budgets["probe_cost_usd"] == 1.0
    assert config.profiles["ci-cheap"].tolerances["defect_recall"] == -0.34
    assert config.briefs["synthetic"] == REPO / "examples/briefs/synthetic.yaml"
    assert config.briefs["polish"].is_file()
    assert [(a.gate, a.decision, a.approver) for a in config.approvals] == [
        ("scope", "approve", "alice"),
        ("model", "accept", "bob"),
        ("signoff", "approve", "bob"),
    ]
    local = config.profiles["local-ollama"]
    assert local.brief == "polish"
    assert local.layers == ["retrieval", "pipeline", "cost"]
    assert local.budgets == {"cost_usd": 0.0, "wall_time_s": 7200}
    assert local.tolerances == {"grounding_pass_rate": -0.20, "retrieval_recall_at_5": -0.05}
    for name, spec in config.profiles.items():
        assert spec.brief in config.briefs
        if name != "local-ollama":
            assert spec.layers == ["retrieval", "pipeline", "challenger", "cost"]


def test_a_config_missing_approvals_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "evals.yml"
    path.parent.mkdir()
    path.write_text(
        "briefs: {synthetic: examples/briefs/synthetic.yaml}\n"
        "profiles:\n  fake: {brief: synthetic, layers: [pipeline]}\n"
    )
    with pytest.raises(EvalConfigError, match="approvals"):
        load_config(path)


def test_invalid_yaml_and_non_mapping_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "evals.yml"
    path.write_text("briefs: [unclosed\n")
    with pytest.raises(EvalConfigError):
        load_config(path)
    path.write_text("- just a list\n")
    with pytest.raises(EvalConfigError):
        load_config(path)


def test_brief_paths_resolve_against_the_repo_root(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "evals.yml"
    path.parent.mkdir()
    path.write_text(
        "briefs: {b: briefs/b.yaml}\n"
        "approvals: [{gate: scope, decision: approve, approver: alice}]\n"
        "profiles:\n  p: {brief: b, layers: [retrieval], ignore: [wall_time_s]}\n"
    )
    config = load_config(path)
    assert config.briefs["b"] == tmp_path / "briefs/b.yaml"
    spec = config.profiles["p"]
    assert (spec.compare, spec.tolerances, spec.budgets) == ("tolerance", {}, {})


def test_unknown_layer_and_unknown_brief_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "evals.yml"
    path.parent.mkdir()
    base = "briefs: {b: b.yaml}\napprovals: []\n"
    path.write_text(base + "profiles:\n  p: {brief: b, layers: [vibes]}\n")
    with pytest.raises(EvalConfigError):
        load_config(path)
    path.write_text(base + "profiles:\n  p: {brief: nope, layers: [cost]}\n")
    with pytest.raises(EvalConfigError, match="nope"):
        load_config(path)
