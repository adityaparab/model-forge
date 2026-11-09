import pytest
from langgraph.runtime import Runtime
from modelforge_core.schemas import BinningTables, CalibrationTests, ValidationReport
from modelforge_lineage.steps import list_tool_calls
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.build import build
from modelforge_orchestrator.nodes.features import features
from modelforge_orchestrator.nodes.validate import validate
from modelforge_orchestrator.schemas import RunState


@pytest.mark.db
def test_features_build_validate_follow_the_example(
    deps: Deps, runtime: Runtime[Deps], prepared_state: RunState
) -> None:
    state = prepared_state.model_copy(
        update={"refs": {**prepared_state.refs, **features(prepared_state, runtime)["refs"]}}
    )
    assert set(state.refs["selection"].summary["selected"]) >= {"f_1", "f_2", "f_3"}
    state = state.model_copy(update={"refs": {**state.refs, **build(state, runtime)["refs"]}})
    assert state.refs["champion"].summary["fit_method"] == "stepwise_wrong_sign"
    assert state.refs["selection"].summary["selected"] == state.refs["champion"].summary["features"]
    assert (
        state.refs["challenger_model"].summary["features"]
        == state.refs["champion"].summary["features"]
    )
    state = state.model_copy(update={"refs": {**state.refs, **validate(state, runtime)["refs"]}})
    report = deps.store.get_model(state.refs["validation"].id, ValidationReport)
    assert len(report.tests) == 13
    assert report.get("disc.auc.oot").status in ("pass", "warn")
    assert report.overall in ("pass", "warn")
    with deps.lineage.connection() as conn:
        calls = list_tool_calls(conn, state.run_id)
    assert [c.tool for c in calls if c.server == "credit-modeling"] == [
        "bin_features_woe",
        "select_features",
        "fit_logistic_pd",
        "fit_gbm_challenger",
        "calibrate",
        "score",
        "score",
    ]
    assert [c.tool for c in calls if c.server == "validation"] == [
        "discrimination_metrics",
        "calibration_tests",
        "stability_tests",
        "sensitivity_analysis",
        "benchmark_models",
        "evaluate_thresholds",
    ]


@pytest.mark.db
def test_exclusions_and_leakage_flags_are_dropped_from_candidates(
    deps: Deps, runtime: Runtime[Deps], prepared_state: RunState
) -> None:
    assert prepared_state.plan is not None
    policy = prepared_state.plan.feature_policy.model_copy(update={"exclusions": ["f_6"]})
    plan = prepared_state.plan.model_copy(update={"feature_policy": policy})
    refs = features(prepared_state.model_copy(update={"plan": plan}), runtime)["refs"]
    tables = deps.store.get_model(refs["binning"].id, BinningTables)
    assert all(f.feature != "f_6" for f in tables.features)
    assert refs["binning"].summary["n_features"] == 5


@pytest.mark.db
def test_validate_without_oot_uses_val(
    deps: Deps, runtime: Runtime[Deps], prepared_state_no_oot: RunState
) -> None:
    state = prepared_state_no_oot
    for node in (features, build, validate):
        state = state.model_copy(update={"refs": {**state.refs, **node(state, runtime)["refs"]}})
    calibration = deps.store.get_model(state.refs["calibration_tests"].id, CalibrationTests)
    assert calibration.split == "val"
    assert state.refs["validation"].summary["statuses"]["disc.auc.oot"] == "not_applicable"
