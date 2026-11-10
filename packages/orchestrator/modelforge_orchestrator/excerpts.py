"""Excerpt policy: which artifacts a document section sees, and the projection it sees them through.

The documentation agent never reads an artifact whole. Each kind is projected (spec 3.6): row
level and bulky fields become ``{"count": n}`` or are dropped, so every citation path the agent
can write still resolves against the full artifact.
"""

import copy
import json
from typing import Any, cast

from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.store import MEDIA_JSON, ArtifactStore
from modelforge_mcp.schemas import SectionSpec

from modelforge_orchestrator.schemas import RefName, RunState

PLAN_ALIAS = "plan"

KIND_TO_REF: dict[str, str] = {
    "profile": "profile",
    "leakage": "leakage",
    "psi": "psi",
    "split_manifest": "split",
    "column_dictionary": "dictionary",
    "binning_tables": "binning",
    "feature_selection": "selection",
    "scorecard": "champion",
    "gbm_model": "challenger_model",
    "calibration_map": "calibration",
    "discrimination": "discrimination",
    "calibration_tests": "calibration_tests",
    "stability": "stability",
    "sensitivity": "sensitivity",
    "benchmark": "benchmark",
    "validation_report": "validation",
    "modeling_plan": PLAN_ALIAS,
}
"""Template artifact kind to the alias (and `RunState.refs` key) it is cited by.

``manifest`` is absent on purpose: the run manifest is sealed after the document is written.
"""

ALIAS_DESCRIPTIONS: dict[str, str] = {
    "profile": "row count, target rate, rows and target rate by vintage, per-feature statistics",
    "leakage": "duplicate row count, AUC threshold and per-feature leakage flags",
    "psi": "base and compare vintages and per-feature population stability index with band",
    "split": "split strategy, seed, out-of-time vintages, row counts and digests per split",
    "dictionary": "column names with their descriptions",
    "binning": "per-feature binning method, trend, monotonicity, IV and bin count",
    "selection": "IV and correlation limits, selected features and dropped features with reasons",
    "champion": "scorecard intercept, per-feature coefficients, points scaling and fit method",
    "challenger_model": "gradient boosting challenger features, parameters and tree count",
    "calibration": "calibration method, the split it was fitted on and its parameters",
    "discrimination": "AUC, Gini and KS with confidence intervals per split",
    "calibration_tests": "per-grade expected and observed defaults, Hosmer-Lemeshow and E/O ratio",
    "stability": "score PSI with band and per-feature CSI between two splits",
    "sensitivity": "coefficient sign checks, WoE monotonicity and bin-shift PD deltas",
    "benchmark": "champion and challenger AUC and KS per split with the AUC gap",
    "validation": "validation tests with value, threshold and status, and the overall outcome",
    "plan": "model name, portfolio, target, intended use, tier, regulatory scope and thresholds",
}
"""One line per alias, for the planner prompt."""


class SectionContext(StrictModel):
    """What one section may cite: aliases to refs, their excerpts, and kinds it cannot have."""

    aliases: dict[str, ArtifactRef]
    excerpts: dict[str, Any]
    unavailable: list[str]


def _count(items: Any) -> Any:
    return {"count": len(items)} if isinstance(items, list) else items


def _count_feature_bins(data: dict[str, Any]) -> None:
    for feature in data.get("features") or []:
        if isinstance(feature, dict) and "bins" in feature:
            feature["bins"] = _count(feature["bins"])


def excerpt(kind: str, data: Any) -> Any:
    """``data`` (an artifact of ``kind``) projected for the documentation agent; a deep copy."""
    data = copy.deepcopy(data)
    if not isinstance(data, dict):
        return data
    projected = cast("dict[str, Any]", data)
    if kind == "split_manifest":
        row_ids = projected.get("row_ids")
        if isinstance(row_ids, dict):
            projected["row_ids"] = {
                split: _count(ids) for split, ids in cast("dict[str, Any]", row_ids).items()
            }
    elif kind == "gbm_model":
        projected.pop("model_text", None)
    elif kind == "calibration_map":
        for key in ("isotonic_x", "isotonic_y"):
            if key in projected:
                projected[key] = _count(projected[key])
    elif kind in ("binning_tables", "scorecard"):
        _count_feature_bins(projected)
    elif kind == "column_dictionary":
        projected["columns"] = [
            {"name": column.get("name"), "description": column.get("description")}
            for column in projected.get("columns") or []
            if isinstance(column, dict)
        ]
    return projected


def for_section(
    section: SectionSpec, state: RunState, store: ArtifactStore, plan_ref: ArtifactRef
) -> SectionContext:
    """The aliases and excerpts ``section`` may cite, given the run so far.

    A kind with no alias, or whose artifact the run has not produced, is ``unavailable``.
    """
    aliases: dict[str, ArtifactRef] = {}
    excerpts: dict[str, Any] = {}
    unavailable: list[str] = []
    for kind in section.artifact_kinds:
        alias = KIND_TO_REF.get(kind)
        if alias is None:
            unavailable.append(kind)
            continue
        ref = plan_ref if alias == PLAN_ALIAS else state.refs.get(cast("RefName", alias))
        if ref is None or ref.media_type != MEDIA_JSON:
            unavailable.append(kind)
            continue
        aliases[alias] = ref
        excerpts[alias] = excerpt(kind, json.loads(store.get(ref.id)))
    return SectionContext(aliases=aliases, excerpts=excerpts, unavailable=unavailable)
