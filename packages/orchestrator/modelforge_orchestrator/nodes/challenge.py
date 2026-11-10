"""`challenge`: the independent challenger agent (spec 3).

Every round runs the six deterministic pre-checks over the rendered document first. Round 1
then reviews the document in two gateway calls (query planning, then the review over the
retrieved passages); later rounds close or reopen pre-check findings deterministically and make
one re-review call over the review findings the developer accepted or disputed. The `findings`
artifact is cumulative, and the node routes to `remediate` while a finding is open and the
remediation bound allows, else to `gate_signoff`. On replay (unless the LLM steps are re-run)
the original run's final findings are copied instead.
"""

import json
from typing import Any, Literal

from langgraph.runtime import Runtime
from langgraph.types import Command
from modelforge_gateway.schemas import CompletionRequest
from modelforge_lineage.findings import insert_findings, set_verdict
from pydantic import BaseModel

from modelforge_orchestrator.agent_schemas import (
    ChallengeQueryPlan,
    FindingDraft,
    ReReviewDraft,
    ReviewDraft,
    Verdict,
)
from modelforge_orchestrator.challenger import (
    CHALLENGER_ALIASES,
    ChallengerContext,
    challenger_context,
    expand_evidence,
    precheck_context,
    validate_re_review,
    validate_review,
)
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import original_manifest
from modelforge_orchestrator.nodes.document import (
    agent_request,
    excerpt_floats_to_four_sig_figs,
    passages_json,
    restricted_terms,
    retrieve_passages,
)
from modelforge_orchestrator.prechecks import PrecheckFinding, run_prechecks
from modelforge_orchestrator.schemas import (
    Finding,
    FindingsList,
    ResponsesList,
    RunState,
)
from modelforge_orchestrator.steps import Step

__all__ = [
    "CHALLENGER_ALIASES",
    "CHECK_PASSES",
    "CHECK_STILL_FIRES",
    "ChallengerContext",
    "challenge",
    "challenger_context",
    "expand_evidence",
    "findings_summary",
    "precheck_context",
    "re_review",
    "reconcile",
    "review",
    "validate_re_review",
    "validate_review",
]

PROMPT_VERSION = 1
SYSTEM_PROMPT = "challenge/system"
PLAN_PROMPT = "challenge/plan_queries"
REVIEW_PROMPT = "challenge/review"
RE_REVIEW_PROMPT = "challenge/re_review"
PLAN_MAX_OUTPUT_TOKENS = 1024
REVIEW_MAX_OUTPUT_TOKENS = 8192
RE_REVIEW_MAX_OUTPUT_TOKENS = 4096
CHECK_PASSES = "check passes after remediation"
CHECK_STILL_FIRES = "check still fires after remediation"
SEVERITY_ORDER = ("critical", "high", "medium", "low")
STATUS_ORDER = ("open", "accepted", "disputed", "closed")
SOURCE_ORDER = ("precheck", "review")
_RE_REVIEW_FIELDS = {
    "id",
    "severity",
    "category",
    "title",
    "description",
    "evidence",
    "required_action",
    "status",
    "response",
    "response_evidence",
}

Route = Literal["remediate", "gate_signoff"]


def _request(
    step: Step,
    prompt_name: str,
    output_schema: type[BaseModel],
    restricted: list[str],
    max_output_tokens: int,
    **values: Any,
) -> CompletionRequest:
    return agent_request(
        step,
        purpose="challenge",
        system=SYSTEM_PROMPT,
        prompt_name=prompt_name,
        version=PROMPT_VERSION,
        output_schema=output_schema,
        restricted_terms=restricted,
        max_output_tokens=max_output_tokens,
        **values,
    )


def _validation_json(ctx: ChallengerContext) -> str:
    return json.dumps(excerpt_floats_to_four_sig_figs(ctx.validation_excerpt), indent=2)


def _plan_json(ctx: ChallengerContext) -> str:
    return json.dumps(ctx.plan.model_dump(mode="json"), indent=2)


def review(
    step: Step,
    ctx: ChallengerContext,
    restricted: list[str],
    prechecks: list[PrecheckFinding],
) -> list[FindingDraft]:
    """Round 1: plan the retrieval, retrieve, and review the document over those passages."""
    prechecks_json = json.dumps([p.model_dump(mode="json") for p in prechecks], indent=2)
    tests: list[dict[str, Any]] = ctx.validation_excerpt.get("tests") or []
    plan_request = _request(
        step,
        PLAN_PROMPT,
        ChallengeQueryPlan,
        restricted,
        PLAN_MAX_OUTPUT_TOKENS,
        sections_json=json.dumps([spec.title for spec in ctx.template.sections], indent=2),
        scope_json=json.dumps(ctx.plan.regulatory_scope, indent=2),
        statuses_json=json.dumps({t["test_id"]: t["status"] for t in tests}, indent=2),
        prechecks_json=prechecks_json,
    )
    plan = step.llm(plan_request).parsed
    if not isinstance(plan, ChallengeQueryPlan):
        raise RuntimeError(f"the query-planning call returned {type(plan).__name__}")
    passages = retrieve_passages(step, plan.queries)
    retrieved = {p.citation_id for p in passages}
    review_request = _request(
        step,
        REVIEW_PROMPT,
        ReviewDraft,
        restricted,
        REVIEW_MAX_OUTPUT_TOKENS,
        document_md=ctx.document_md,
        validation_json=_validation_json(ctx),
        plan_json=_plan_json(ctx),
        passages_json=passages_json(passages),
        prechecks_json=prechecks_json,
    )
    draft = step.llm(
        review_request, validate=lambda parsed: validate_review(parsed, ctx, retrieved)
    ).parsed
    if not isinstance(draft, ReviewDraft):
        raise RuntimeError(f"the review call returned {type(draft).__name__}")
    return draft.findings


def re_review(
    step: Step,
    ctx: ChallengerContext,
    findings: list[Finding],
    changed: dict[str, str],
    restricted: list[str],
) -> dict[str, Verdict]:
    """Later rounds: one verdict per review finding the developer accepted or disputed.

    No call is made, and ``{}`` returned, when there is no such finding.
    """
    under_review = [
        f for f in findings if f.source == "review" and f.status in ("accepted", "disputed")
    ]
    if not under_review:
        return {}
    request = _request(
        step,
        RE_REVIEW_PROMPT,
        ReReviewDraft,
        restricted,
        RE_REVIEW_MAX_OUTPUT_TOKENS,
        findings_json=json.dumps(
            [f.model_dump(mode="json", include=_RE_REVIEW_FIELDS) for f in under_review],
            indent=2,
        ),
        changed_sections_json=json.dumps(changed, indent=2),
        validation_json=_validation_json(ctx),
        plan_json=_plan_json(ctx),
    )
    expected = {f.id for f in under_review}
    draft = step.llm(request, validate=lambda parsed: validate_re_review(parsed, expected)).parsed
    if not isinstance(draft, ReReviewDraft):
        raise RuntimeError(f"the re-review call returned {type(draft).__name__}")
    return {v.finding_id: v for v in draft.verdicts}


def _from_precheck(
    precheck: PrecheckFinding, finding_id: str, round: int, ctx: ChallengerContext
) -> Finding:
    return Finding(
        id=finding_id,
        round=round,
        severity=precheck.severity,
        category=precheck.category,
        title=precheck.title,
        description=precheck.description,
        evidence=expand_evidence(precheck.evidence, ctx),
        required_action=precheck.required_action,
        source="precheck",
        check_key=precheck.check_key,
    )


def _from_draft(draft: FindingDraft, finding_id: str, ctx: ChallengerContext) -> Finding:
    return Finding(
        id=finding_id,
        round=1,
        severity=draft.severity,
        category=draft.category,
        title=draft.title,
        description=draft.description,
        evidence=expand_evidence(draft.evidence, ctx),
        required_action=draft.required_action,
    )


def reconcile(
    previous: list[Finding],
    prechecks: list[PrecheckFinding],
    verdicts: dict[str, Verdict],
    round: int,
    ctx: ChallengerContext,
) -> list[Finding]:
    """The cumulative findings after re-review round ``round`` (spec 3.4).

    A closed finding stays as it is. A pre-check finding whose check no longer fires closes; one
    whose check still fires is ``open`` again (``disputed`` stays ``disputed``). A review finding
    with a verdict takes the verdict's status and note. A check that fires with no live finding
    of its key raises a new pre-check finding with the next id.
    """
    fired = {p.check_key for p in prechecks}
    live: set[str] = set()
    reconciled: list[Finding] = []
    for finding in previous:
        if finding.status == "closed":
            reconciled.append(finding)
        elif finding.check_key is not None and finding.check_key in fired:
            live.add(finding.check_key)
            status = "disputed" if finding.status == "disputed" else "open"
            reconciled.append(
                finding.model_copy(update={"status": status, "verdict": CHECK_STILL_FIRES})
            )
        elif finding.source == "precheck":
            reconciled.append(
                finding.model_copy(
                    update={"status": "closed", "verdict": CHECK_PASSES, "closed_round": round}
                )
            )
        elif finding.id in verdicts:
            verdict = verdicts[finding.id]
            closed_round = round if verdict.status == "closed" else None
            reconciled.append(
                finding.model_copy(
                    update={
                        "status": verdict.status,
                        "verdict": verdict.note,
                        "closed_round": closed_round,
                    }
                )
            )
        else:
            reconciled.append(finding)
    for precheck in prechecks:
        if precheck.check_key in live:
            continue
        live.add(precheck.check_key)
        reconciled.append(_from_precheck(precheck, f"F-{len(reconciled) + 1}", round, ctx))
    return reconciled


def _counts(values: list[str], order: tuple[str, ...]) -> dict[str, int]:
    return {key: n for key in order if (n := values.count(key))}


def findings_summary(findings: list[Finding], round: int) -> dict[str, Any]:
    """The `findings` ref summary; ``max_severity`` is over every finding, closed ones too."""
    severities = [f.severity for f in findings]
    return {
        "n_findings": len(findings),
        "max_severity": next((s for s in SEVERITY_ORDER if s in severities), None),
        "by_severity": _counts(severities, SEVERITY_ORDER),
        "by_status": _counts([f.status for f in findings], STATUS_ORDER),
        "by_source": _counts([f.source for f in findings], SOURCE_ORDER),
        "round": round,
    }


def _changed_sections(deps: Deps, state: RunState, ctx: ChallengerContext) -> dict[str, str]:
    """The sections regenerated in the latest remediation round, with their current bodies."""
    ref = state.refs.get("responses")
    if ref is None:
        return {}
    responses = deps.store.get_model(ref.id, ResponsesList).responses
    if not responses:
        return {}
    latest = max(r.round for r in responses)
    ids = dict.fromkeys(s for r in responses if r.round == latest for s in r.sections_regenerated)
    return {i: ctx.sections[i] for i in ids if i in ctx.sections}


def _row(state: RunState, finding: Finding) -> dict[str, Any]:
    return {**finding.model_dump(mode="json", exclude={"id"}), "id": f"{state.run_id}/{finding.id}"}


def _write_rows(
    deps: Deps, state: RunState, previous: list[Finding], findings: list[Finding]
) -> None:
    """Insert the new findings' rows and record each changed status or verdict."""
    before = {f.id: f for f in previous}
    new = [f for f in findings if f.id not in before]
    changed = [
        f
        for f in findings
        if f.id in before and (f.status, f.verdict) != (before[f.id].status, before[f.id].verdict)
    ]
    with deps.lineage.transaction() as conn:
        insert_findings(conn, state.run_id, [_row(state, f) for f in new])
        for finding in changed:
            set_verdict(
                conn,
                state.run_id,
                f"{state.run_id}/{finding.id}",
                status=finding.status,
                verdict=finding.verdict or "",
                closed_round=finding.closed_round,
            )


def _findings(step: Step, deps: Deps, state: RunState, round: int) -> list[Finding]:
    """This round's cumulative findings, their rows written."""
    ctx = challenger_context(state, deps, step)
    restricted = restricted_terms(deps, state)
    prechecks = run_prechecks(precheck_context(ctx, deps))
    if round == 1:
        previous: list[Finding] = []
        drafts = review(step, ctx, restricted, prechecks)
        findings = [
            _from_precheck(p, f"F-{n}", round, ctx) for n, p in enumerate(prechecks, start=1)
        ]
        findings += [
            _from_draft(d, f"F-{n}", ctx) for n, d in enumerate(drafts, start=len(findings) + 1)
        ]
    else:
        previous = deps.store.get_model(state.refs["findings"].id, FindingsList).findings
        changed = _changed_sections(deps, state, ctx)
        verdicts = re_review(step, ctx, previous, changed, restricted)
        findings = reconcile(previous, prechecks, verdicts, round, ctx)
    _write_rows(deps, state, previous, findings)
    return findings


def challenge(state: RunState, runtime: Runtime[Deps]) -> Command[Route]:
    deps = runtime.context
    round = state.counters.challenge_rounds + 1
    with Step(deps, state, "challenge") as step:
        goto: Route
        if state.replay_of is not None and not state.rerun_llm:
            manifest = original_manifest(state, deps)
            original = manifest.refs["findings"]
            copied = deps.store.get_model(original.id, FindingsList)
            ref = step.store(copied, kind="findings", summary=original.summary)
            with deps.lineage.transaction() as conn:
                insert_findings(conn, state.run_id, [_row(state, f) for f in copied.findings])
            again = "responses" in manifest.refs and state.counters.remediation_rounds == 0
            goto = "remediate" if again else "gate_signoff"
        else:
            findings = _findings(step, deps, state, round)
            ref = step.store(
                FindingsList(round=round, findings=findings),
                kind="findings",
                summary=findings_summary(findings, round),
            )
            remediable = state.counters.remediation_rounds < state.max_remediation_rounds
            open_ = any(f.status == "open" for f in findings)
            goto = "remediate" if open_ and remediable else "gate_signoff"
        update: dict[str, Any] = {
            "refs": {"findings": ref},
            "counters": state.counters.model_copy(update={"challenge_rounds": round}),
            "cost_usd": state.cost_usd + step.spent_usd,
        }
        step.complete(update)
    return Command(goto=goto, update=update)
