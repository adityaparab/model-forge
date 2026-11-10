"""The eval report, its comparison with a committed baseline, and the Markdown summary."""

from fnmatch import fnmatchcase
from typing import Any

from modelforge_core.schemas import StrictModel

from modelforge_evals.config import ProfileEvalSpec

MetricValue = float | int | bool | str | None


class EvalReport(StrictModel):
    """One profile's eval results. No wall-clock timestamps, so equal runs give equal reports."""

    profile: str
    brief: str
    run_id: str | None
    models: dict[str, str]
    """Purpose to ``provider/model``."""
    orchestrator_version: str
    prompts: dict[str, str]
    """``name@version`` to prompt hash, from the run's `llm_calls` rows."""
    metrics: dict[str, MetricValue]
    """Flattened metrics (nested keys joined with a dot)."""
    defect_recall_by_class: dict[str, float | None]
    probe_run_ids: list[str]
    approvals: list[str]
    """``gate decision approver`` as the harness recorded them."""
    misses: list[str]
    """Retrieval golden questions missed at k = 5."""


class Regression(StrictModel):
    metric: str
    baseline: Any
    value: Any
    rule: str


def baseline_of(report: EvalReport) -> dict[str, Any]:
    """The part of ``report`` committed as a baseline."""
    return {"profile": report.profile, "metrics": dict(report.metrics)}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _exact(metrics: dict[str, Any], base: dict[str, Any], ignore: list[str]) -> list[Regression]:
    def ignored(name: str) -> bool:
        return any(fnmatchcase(name, pattern) for pattern in ignore)

    out: list[Regression] = []
    for name, expected in base.items():
        if ignored(name):
            continue
        if name not in metrics:
            out.append(Regression(metric=name, baseline=expected, value=None, rule="missing"))
        elif metrics[name] != expected:
            out.append(
                Regression(metric=name, baseline=expected, value=metrics[name], rule="exact")
            )
    for name, value in metrics.items():
        if name not in base and not ignored(name):
            out.append(Regression(metric=name, baseline=None, value=value, rule="missing"))
    return out


def _tolerances(
    metrics: dict[str, Any], base: dict[str, Any], tolerances: dict[str, float]
) -> list[Regression]:
    out: list[Regression] = []
    for name, tol in tolerances.items():
        expected, value = base.get(name), metrics.get(name)
        low, high = _number(expected), _number(value)
        if low is None or high is None:
            out.append(Regression(metric=name, baseline=expected, value=value, rule="missing"))
            continue
        if tol < 0 and not high >= low + tol:
            rule = f">= baseline - {-tol}"
        elif tol >= 0 and not high <= low + tol:
            rule = f"<= baseline + {tol}"
        else:
            continue
        out.append(Regression(metric=name, baseline=expected, value=value, rule=rule))
    return out


def _budgets(metrics: dict[str, Any], budgets: dict[str, float]) -> list[Regression]:
    out: list[Regression] = []
    for name, budget in budgets.items():
        value = _number(metrics.get(name))
        if value is None:
            out.append(
                Regression(metric=name, baseline=budget, value=metrics.get(name), rule="missing")
            )
        elif value > budget:
            out.append(
                Regression(
                    metric=name, baseline=budget, value=metrics[name], rule=f"budget <= {budget}"
                )
            )
    return out


def compare(
    report: EvalReport, baseline: dict[str, Any], spec: ProfileEvalSpec
) -> list[Regression]:
    """The regressions of ``report`` against ``baseline`` (as `baseline_of` writes it).

    Under ``exact`` every metric of either side whose name matches no ``ignore`` glob must be
    present on both and equal. Under ``tolerance`` only the metrics named in ``tolerances``
    are compared. ``budgets`` apply under both. A metric missing from either side where a rule
    needs it is a regression with rule ``missing``.
    """
    base = dict(baseline.get("metrics", {}))
    metrics = dict(report.metrics)
    if spec.compare == "exact":
        found = _exact(metrics, base, spec.ignore)
    else:
        found = _tolerances(metrics, base, spec.tolerances)
    return found + _budgets(metrics, spec.budgets)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def markdown_summary(report: EvalReport, regressions: list[Regression]) -> str:
    """A Markdown table of the report's metrics followed by its regressions."""
    lines = [
        f"# Evals: profile `{report.profile}`, brief `{report.brief}`",
        "",
        f"Run `{report.run_id or '-'}`, orchestrator {report.orchestrator_version}.",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    lines += [f"| {name} | {_cell(value)} |" for name, value in sorted(report.metrics.items())]
    if report.defect_recall_by_class:
        lines += ["", "| defect class | recall |", "|---|---|"]
        lines += [
            f"| {name} | {_cell(value)} |"
            for name, value in sorted(report.defect_recall_by_class.items())
        ]
    if report.misses:
        lines += ["", "## Retrieval misses", ""]
        lines += [f"- {question}" for question in report.misses]
    lines += ["", "## Regressions", ""]
    if regressions:
        lines += [
            f"- `{r.metric}`: {_cell(r.value)} (baseline {_cell(r.baseline)}; rule {r.rule})"
            for r in regressions
        ]
    else:
        lines.append("No regressions.")
    return "\n".join(lines) + "\n"
