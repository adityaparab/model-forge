"""The challenger: pre-checks, the two-call review, the re-review, reconciliation and routing."""

from typing import Any, Literal

import pytest
import yaml
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.runtime import Runtime
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.findings import list_findings
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.store import MEDIA_MARKDOWN, ArtifactStore
from modelforge_mcp.schemas import CompletenessReport, SectionSpec, TemplateSections
from modelforge_mcp.servers.doc_renderer import BANNER
from modelforge_orchestrator.agent_schemas import (
    ChallengeQueryPlan,
    FindingDraft,
    ReReviewDraft,
    ReviewDraft,
    Verdict,
)
from modelforge_orchestrator.briefs import parse_brief, plan_from_brief
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import invoke_node, original_manifest
from modelforge_orchestrator.nodes.challenge import (
    ChallengerContext,
    challenge,
    reconcile,
    validate_re_review,
    validate_review,
)
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.prechecks import PrecheckFinding
from modelforge_orchestrator.schemas import Counters, Finding, FindingsList, RunState
from modelforge_orchestrator.sections import split_sections
from pydantic import BaseModel

from .conftest import REPO, SYNTHETIC_BRIEF, RuntimeWithModel, apply_node, make_run

VALIDATION_ID = "a" * 64
PLAN_ID = "b" * 64


def _template() -> TemplateSections:
    entries = yaml.safe_load((REPO / "templates/sr11-7-dev-doc/sections.yaml").read_text())
    return TemplateSections(
        template_id="sr11-7-dev-doc", sections=[SectionSpec(**e) for e in entries]
    )


@pytest.fixture
def ctx() -> ChallengerContext:
    """A context over the real template and plan, with a two-test validation excerpt."""
    template = _template()
    tests = [
        {
            "test_id": test_id,
            "name": name,
            "status": status,
            "value": 0.5,
            "threshold": "t",
            "message": "m",
        }
        for test_id, name, status in (
            ("disc.auc.oot", "OOT AUC", "pass"),
            ("cal.binomial", "Binomial", "warn"),
        )
    ]
    validation = {"tests": tests, "overall": "pass"}
    return ChallengerContext(
        document_md="# doc\n",
        sections={},
        template=template,
        completeness=CompletenessReport(
            template_id="sr11-7-dev-doc",
            document_id="c" * 64,
            missing_sections=[],
            unresolved_markers=[],
            uncited_regulatory_sentences=[],
            complete=True,
        ),
        validation_excerpt=validation,
        validation_id=VALIDATION_ID,
        plan=plan_from_brief(parse_brief(SYNTHETIC_BRIEF.read_bytes())),
        plan_id=PLAN_ID,
        artifact_kinds={},
        passages={"k_known": ("sr11-7", "text")},
    )


def _draft(evidence: list[str]) -> ReviewDraft:
    return ReviewDraft(
        findings=[
            FindingDraft(
                severity="medium",
                category="monitoring",
                title="t",
                description="d",
                evidence=evidence,
                required_action="a",
            )
        ]
    )


class ChallengeStub(BaseChatModel):
    """Plans one query, then answers the review request with a refusal or bad evidence."""

    review: Literal["refuse", "bad_evidence"]

    @property
    def _llm_type(self) -> str:
        return "challenge-stub"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        def respond(model_input: LanguageModelInput) -> Any:
            parsed: BaseModel | None
            stop_reason = "end_turn"
            if schema is ChallengeQueryPlan:
                parsed = ChallengeQueryPlan(queries=["effective challenge"])
            elif self.review == "refuse":
                parsed, stop_reason = None, "refusal"
            else:
                parsed = _draft(["nope"])
            raw = AIMessage(
                content=parsed.model_dump_json() if parsed is not None else "",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"stop_reason": stop_reason},
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return RunnableLambda(respond)


@pytest.fixture
def documented_state(validated_state: RunState, runtime: Runtime[Deps]) -> RunState:
    return apply_node(validated_state, document(validated_state, runtime))


def _bannered_limitations(state: RunState, store: ArtifactStore) -> RunState:
    """``state`` whose `document_md` has the limitations body replaced by the banner."""
    markdown = store.get(state.refs["document_md"].id).decode()
    body = split_sections(markdown, _template())["limitations"]
    ref = store.put(
        markdown.replace(body, BANNER).encode(), kind="document_md", media_type=MEDIA_MARKDOWN
    )
    return state.model_copy(update={"refs": {**state.refs, "document_md": ref}})


@pytest.mark.db
def test_challenge_round_one_raises_prechecks_then_review_findings(
    documented_state: RunState, deps: Deps, runtime: Runtime[Deps], db: Database
) -> None:
    state = _bannered_limitations(documented_state, deps.store)
    command = challenge(state, runtime)
    update = command.update
    assert update is not None
    findings = deps.store.get_model(update["refs"]["findings"].id, FindingsList)
    ids = [f.id for f in findings.findings]
    assert ids == [f"F-{i}" for i in range(1, len(ids) + 1)] and findings.round == 1
    sources = [f.source for f in findings.findings]
    assert sources == sorted(sources, key=lambda s: s != "precheck")
    first = findings.findings[0]
    assert (first.source, first.check_key) == ("precheck", "missing_section:limitations")
    assert first.severity == "critical" and first.evidence == ["limitations"]
    assert all(f.round == 1 and f.status == "open" for f in findings.findings)
    assert all((f.check_key is not None) == (f.source == "precheck") for f in findings.findings)
    reviews = [f for f in findings.findings if f.source == "review"]
    assert {f.title for f in reviews} >= {
        "Monitoring thresholds are not quantified",
        "Approver identities are not named in the governance section",
    }
    with db.connection() as conn:
        plan_ref = runs.get_run(conn, state.run_id).plan_ref
    monitoring = next(f for f in reviews if f.title.startswith("Monitoring"))
    assert monitoring.evidence == ["monitoring", f"[A:{plan_ref}#thresholds.psi_warn]"]
    for finding in findings.findings:
        assert not any("[A:validation#" in e or "[A:plan#" in e for e in finding.evidence)
    assert command.goto == "remediate"
    assert update["counters"] == state.counters.model_copy(update={"challenge_rounds": 1})
    summary = update["refs"]["findings"].summary
    assert summary["by_status"] == {"open": len(ids)} and summary["round"] == 1
    assert summary["n_findings"] == len(ids)
    assert summary["by_source"] == {
        k: v for k, v in {"precheck": len(ids) - len(reviews), "review": len(reviews)}.items() if v
    }
    assert sum(summary["by_severity"].values()) == len(ids)
    assert update["cost_usd"] >= state.cost_usd
    with db.connection() as conn:
        rows = list_findings(conn, state.run_id)
        calls = [c for c in list_llm_calls(conn, state.run_id) if c.purpose == "challenge"]
    assert {r["id"] for r in rows} == {f"{state.run_id}/{i}" for i in ids}
    assert all(r["status"] == "open" for r in rows)
    assert {r["source"] for r in rows} == set(sources)
    assert [c.prompt_name for c in calls] == ["challenge/plan_queries", "challenge/review"]


@pytest.mark.db
def test_invoke_node_returns_the_update_and_the_goto(
    synthetic_state: RunState, documented_state: RunState, deps: Deps
) -> None:
    update, goto = invoke_node(intake, synthetic_state, deps)
    assert goto is None and update["max_remediation_rounds"] == 2
    update, goto = invoke_node(challenge, documented_state, deps)
    assert goto == "remediate" and update["refs"]["findings"].kind == "findings"


@pytest.mark.db
def test_challenge_routes_to_gate_when_bound_is_zero(
    documented_state: RunState, runtime: Runtime[Deps]
) -> None:
    state = documented_state.model_copy(update={"max_remediation_rounds": 0})
    command = challenge(state, runtime)
    assert command.goto == "gate_signoff"
    assert command.update is not None and command.update["counters"].challenge_rounds == 1


@pytest.mark.db
def test_challenge_parks_on_refusal(
    documented_state: RunState, runtime_with_model: RuntimeWithModel, db: Database
) -> None:
    with pytest.raises(GatewayError) as caught:
        challenge(documented_state, runtime_with_model(ChallengeStub(review="refuse")))
    assert caught.value.kind == "refusal"
    with db.connection() as conn:
        assert list_findings(conn, documented_state.run_id) == []


@pytest.mark.db
def test_challenge_parks_after_persistent_bad_evidence(
    documented_state: RunState, runtime_with_model: RuntimeWithModel, db: Database
) -> None:
    with pytest.raises(GatewayError) as caught:
        challenge(documented_state, runtime_with_model(ChallengeStub(review="bad_evidence")))
    assert caught.value.kind == "schema" and "nope" in caught.value.message
    with db.connection() as conn:
        reviews = [
            c
            for c in list_llm_calls(conn, documented_state.run_id)
            if c.prompt_name == "challenge/review"
        ]
        assert list_findings(conn, documented_state.run_id) == []
    assert [c.schema_valid for c in reviews] == [False, False, False]


def test_review_hook_names_each_bad_evidence_entry(ctx: ChallengerContext) -> None:
    bad = ["nope", "[A:profile#n_rows]", "[A:validation#tests[zz].status]", "[K:k_unknown]"]
    message = validate_review(_draft(["monitoring", *bad]), ctx, {"k_known"})
    assert message is not None
    assert all(entry in message for entry in bad)
    assert "'monitoring'" not in message
    good = [
        "monitoring",
        "[A:validation#tests[cal.binomial].status]",
        "[A:plan#thresholds.psi_warn]",
        "[K:k_known]",
    ]
    assert validate_review(_draft(good), ctx, {"k_known"}) is None
    assert validate_review(_draft([]), ctx, set()) is not None
    # A passage known to the index but not retrieved in this round is still rejected.
    assert validate_review(_draft(["[K:k_known]"]), ctx, set()) is not None


def test_re_review_hook_requires_one_verdict_per_finding() -> None:
    def draft(*ids: str) -> ReReviewDraft:
        return ReReviewDraft(
            verdicts=[Verdict(finding_id=i, status="closed", note="n") for i in ids]
        )

    expected = {"F-1", "F-2"}
    assert validate_re_review(draft("F-1", "F-2"), expected) is None
    missing = validate_re_review(draft("F-1"), expected)
    assert missing is not None and "F-2" in missing
    duplicate = validate_re_review(draft("F-1", "F-1", "F-2"), expected)
    assert duplicate is not None and "F-1" in duplicate
    unknown = validate_re_review(draft("F-1", "F-2", "F-9"), expected)
    assert unknown is not None and "F-9" in unknown


def _finding(
    n: int, status: Literal["open", "accepted", "disputed", "closed"], **kw: Any
) -> Finding:
    fields: dict[str, Any] = {
        "id": f"F-{n}",
        "round": 1,
        "severity": "high",
        "category": "documentation",
        "title": f"finding {n}",
        "description": "d",
        "evidence": ["performance"],
        "required_action": "a",
        "status": status,
        "response": "r",
    }
    return Finding(**{**fields, **kw})


def _precheck(key: str) -> PrecheckFinding:
    return PrecheckFinding(
        check_key=key,
        severity="high",
        category="performance",
        title=f"check {key}",
        description="d",
        evidence=["performance", "[A:validation#tests[cal.binomial].status]"],
        required_action="a",
    )


def test_re_review_closes_prechecks_deterministically_and_applies_verdicts(
    ctx: ChallengerContext,
) -> None:
    previous = [
        _finding(1, "accepted", source="precheck", check_key="undiscussed_test:a"),
        _finding(2, "accepted", source="precheck", check_key="bannered_section:data"),
        _finding(3, "disputed", source="precheck", check_key="reverify:data"),
        _finding(4, "accepted"),
        _finding(5, "disputed"),
        _finding(6, "closed", verdict="earlier", closed_round=1),
    ]
    prechecks = [
        _precheck("bannered_section:data"),
        _precheck("reverify:data"),
        _precheck("undiscussed_test:cal.binomial"),
    ]
    verdicts = {
        "F-4": Verdict(finding_id="F-4", status="closed", note="fixed"),
        "F-5": Verdict(finding_id="F-5", status="disputed", note="dispute noted"),
    }
    out = {f.id: f for f in reconcile(previous, prechecks, verdicts, 2, ctx)}
    assert list(out) == ["F-1", "F-2", "F-3", "F-4", "F-5", "F-6", "F-7"]
    assert out["F-1"].status == "closed" and out["F-1"].closed_round == 2
    assert out["F-1"].verdict == "check passes after remediation"
    assert out["F-2"].status == "open" and out["F-2"].closed_round is None
    assert out["F-2"].verdict is not None
    assert out["F-3"].status == "disputed"
    assert out["F-4"].status == "closed" and out["F-4"].closed_round == 2
    assert out["F-4"].verdict == "fixed"
    assert out["F-5"].status == "disputed" and out["F-5"].verdict == "dispute noted"
    assert out["F-6"] == previous[5]
    new = out["F-7"]
    assert new.round == 2 and new.status == "open" and new.source == "precheck"
    assert new.check_key == "undiscussed_test:cal.binomial"
    assert new.evidence == ["performance", f"[A:{VALIDATION_ID}#tests[cal.binomial].status]"]


@pytest.mark.db
def test_challenge_replay_copies_findings_and_routes(
    db: Database, completed_run_id: str, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    # ``db`` truncates the tables, so it must come before ``completed_run_id``.
    state = make_run(db, store, SYNTHETIC_BRIEF, replay_of=completed_run_id)
    original = original_manifest(state, runtime.context)
    assert "responses" in original.refs
    command = challenge(state, runtime)
    assert command.goto == "remediate" and command.update is not None
    ref = command.update["refs"]["findings"]
    assert ref.id == original.refs["findings"].id
    assert ref.summary == original.refs["findings"].summary
    copied = store.get_model(ref.id, FindingsList)
    with db.connection() as conn:
        rows = list_findings(conn, state.run_id)
        assert list_llm_calls(conn, state.run_id) == []
    assert {r["id"] for r in rows} == {f"{state.run_id}/{f.id}" for f in copied.findings}
    after = state.model_copy(update={"counters": Counters(remediation_rounds=1)})
    assert challenge(after, runtime).goto == "gate_signoff"
