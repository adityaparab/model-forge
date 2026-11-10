import hashlib
from pathlib import Path

import pytest
from modelforge_core.standards import DEFAULT_THRESHOLDS
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.briefs import (
    BriefError,
    brief_hash,
    load_brief,
    parse_brief,
    plan_from_brief,
)

REPO = Path(__file__).resolve().parents[3]


def test_example_briefs_load_and_map_to_plans() -> None:
    brief, data = load_brief(REPO / "examples/briefs/polish-pd.yaml")
    assert brief.dataset == DatasetRef(adapter="polish_bankruptcy", seed=42)
    assert brief_hash(data) == hashlib.sha256(data).hexdigest()
    plan = plan_from_brief(brief)
    assert plan.model_name == "polish_bankruptcy_pd" and plan.thresholds == DEFAULT_THRESHOLDS
    assert plan.vintage_policy.oot_vintages == [5]
    synthetic = load_brief(REPO / "examples/briefs/synthetic.yaml")[0]
    assert synthetic.dataset.adapter == "synthetic_credit"
    assert plan_from_brief(synthetic).tier == 3


def test_threshold_overrides_and_errors() -> None:
    text = (
        (REPO / "examples/briefs/synthetic.yaml")
        .read_text()
        .replace("thresholds: {}", "thresholds: {auc_min: 0.75}")
    )
    assert plan_from_brief(parse_brief(text.encode())).thresholds.auc_min == 0.75
    with pytest.raises(BriefError):
        parse_brief(b"model_name: x\n")
    with pytest.raises(BriefError):
        parse_brief(b": not yaml :")
    with pytest.raises(BriefError):
        parse_brief(b"- a list\n")


def test_unknown_threshold_key_is_rejected() -> None:
    text = (
        (REPO / "examples/briefs/synthetic.yaml")
        .read_text()
        .replace("thresholds: {}", "thresholds: {auc_minimum: 0.75}")
    )
    with pytest.raises(ValueError):
        plan_from_brief(parse_brief(text.encode()))


def test_remediation_rounds_bound() -> None:
    text = (REPO / "examples/briefs/synthetic.yaml").read_text()
    assert parse_brief(text.encode()).remediation_rounds == 2
    assert parse_brief((text + "remediation_rounds: 3\n").encode()).remediation_rounds == 3
    for bad in ("4", "-1"):
        with pytest.raises(BriefError):
            parse_brief((text + f"remediation_rounds: {bad}\n").encode())
