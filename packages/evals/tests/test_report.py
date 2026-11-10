"""The eval report, baseline comparison and the Markdown summary."""

from typing import Any

from modelforge_evals.config import ProfileEvalSpec
from modelforge_evals.report import EvalReport, Regression, baseline_of, compare, markdown_summary


def _report(**metrics: Any) -> EvalReport:
    return EvalReport(
        profile="fake",
        brief="synthetic",
        run_id="run_000000000001",
        models={"document": "fake/scripted-v1"},
        orchestrator_version="0.1.0",
        prompts={"document@1": "a" * 64},
        metrics=metrics,
        defect_recall_by_class={"removed_section": 1.0, "swapped_number": None},
        probe_run_ids=[],
        approvals=["scope approve alice"],
        misses=[],
    )


def _spec(**fields: Any) -> ProfileEvalSpec:
    return ProfileEvalSpec.model_validate({"brief": "synthetic", "layers": ["cost"], **fields})


def test_exact_flags_a_changed_metric_and_ignores_timing() -> None:
    spec = _spec(compare="exact", ignore=["wall_time_s", "wall_time_by_node_s.*"])
    baseline = baseline_of(
        _report(n_findings=3, wall_time_s=1.0, **{"wall_time_by_node_s.challenge": 0.2})
    )
    assert baseline == {
        "profile": "fake",
        "metrics": {"n_findings": 3, "wall_time_s": 1.0, "wall_time_by_node_s.challenge": 0.2},
    }
    same = _report(n_findings=3, wall_time_s=9.0, **{"wall_time_by_node_s.challenge": 5.0})
    assert compare(same, baseline, spec) == []
    changed = _report(n_findings=4, wall_time_s=1.0, **{"wall_time_by_node_s.challenge": 0.2})
    assert compare(changed, baseline, spec) == [
        Regression(metric="n_findings", baseline=3, value=4, rule="exact")
    ]


def test_missing_metrics_are_regressions() -> None:
    spec = _spec(compare="exact")
    baseline = {"profile": "fake", "metrics": {"n_findings": 3}}
    assert compare(_report(), baseline, spec) == [
        Regression(metric="n_findings", baseline=3, value=None, rule="missing")
    ]
    assert compare(_report(n_findings=3, n_new=1), baseline, spec) == [
        Regression(metric="n_new", baseline=None, value=1, rule="missing")
    ]
    tolerant = _spec(tolerances={"grounding_pass_rate": -0.1})
    assert compare(_report(grounding_pass_rate=1.0), {"metrics": {}}, tolerant) == [
        Regression(metric="grounding_pass_rate", baseline=None, value=1.0, rule="missing")
    ]


def test_tolerances() -> None:
    spec = _spec(tolerances={"grounding_pass_rate": -0.10, "cost_usd": 1.5})
    baseline = {"profile": "default", "metrics": {"grounding_pass_rate": 1.0, "cost_usd": 2.0}}
    fell_far = compare(_report(grounding_pass_rate=0.8, cost_usd=2.0), baseline, spec)
    assert [r.metric for r in fell_far] == ["grounding_pass_rate"]
    assert fell_far[0].rule == ">= baseline - 0.1"
    assert compare(_report(grounding_pass_rate=0.95, cost_usd=3.4), baseline, spec) == []
    rose = compare(_report(grounding_pass_rate=1.0, cost_usd=3.6), baseline, spec)
    assert [(r.metric, r.rule) for r in rose] == [("cost_usd", "<= baseline + 1.5")]


def test_budgets() -> None:
    spec = _spec(budgets={"cost_usd": 8.0})
    assert compare(_report(cost_usd=8.0), {"metrics": {}}, spec) == []
    over = compare(_report(cost_usd=8.5), {"metrics": {}}, spec)
    assert over == [Regression(metric="cost_usd", baseline=8.0, value=8.5, rule="budget <= 8.0")]


def test_markdown_summary_lists_metrics_and_regressions() -> None:
    report = _report(grounding_pass_rate=0.8, cost_usd=0.0, signoff_decision=None)
    regression = Regression(
        metric="grounding_pass_rate", baseline=1.0, value=0.8, rule=">= baseline - 0.1"
    )
    text = markdown_summary(report, [regression])
    for name in ("grounding_pass_rate", "cost_usd", "signoff_decision", "fake", "synthetic"):
        assert name in text
    assert "| metric | value |" in text
    assert "## Regressions" in text
    assert ">= baseline - 0.1" in text
    assert "No regressions." in markdown_summary(report, [])


def test_report_round_trips_through_json() -> None:
    report = _report(n_findings=3, signoff_decision="approve")
    assert EvalReport.model_validate_json(report.model_dump_json()) == report
