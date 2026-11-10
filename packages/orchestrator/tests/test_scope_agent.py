"""The scope agent: one gateway call over the brief, the defaults and knowledge passages."""

from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.runtime import Runtime
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage.db import Database
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.agent_schemas import ScopeNotes, ScopeProposal
from modelforge_orchestrator.briefs import Brief, parse_brief, plan_from_brief
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import brief_of, original_manifest
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.scope import scope, scope_invariants
from modelforge_orchestrator.schemas import RunState, apply_update
from pydantic import BaseModel

from .conftest import SYNTHETIC_BRIEF, RuntimeWithModel, make_run


@pytest.fixture
def brief() -> Brief:
    return parse_brief(SYNTHETIC_BRIEF.read_bytes())


class StubModel(BaseChatModel):
    """Answers every structured call with ``proposal``."""

    proposal: ScopeProposal

    @property
    def _llm_type(self) -> str:
        return "stub"

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
            raw = AIMessage(
                content=self.proposal.model_dump_json(),
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"stop_reason": "end_turn"},
            )
            return {"raw": raw, "parsed": self.proposal, "parsing_error": None}

        return RunnableLambda(respond)


@pytest.mark.db
def test_scope_agent_plans_from_brief_with_notes_and_index(
    synthetic_state: RunState, deps: Deps, runtime: Runtime[Deps], db: Database
) -> None:
    state = apply_update(synthetic_state, intake(synthetic_state, runtime))
    update = scope(state, runtime)
    # The fake provider reproduces the SP2 mapping from the brief.
    assert update["plan"].model_name == "synthetic_pd"
    assert update["plan"] == plan_from_brief(brief_of(state, deps.store))
    assert set(update["refs"]) == {"knowledge_index", "scope_notes"}
    assert update["refs"]["knowledge_index"].kind == "knowledge_index"
    notes = deps.store.get_model(update["refs"]["scope_notes"].id, ScopeNotes)
    assert notes.passages and notes.rationale.endswith(f"[K:{notes.passages[0]}].")
    assert update["refs"]["scope_notes"].summary == {
        "rationale": notes.rationale,
        "assumptions": notes.assumptions,
    }
    with db.connection() as conn:
        calls = list_llm_calls(conn, state.run_id)
    assert [c.purpose for c in calls] == ["scope"] and calls[0].schema_valid
    assert calls[0].prompt_name == "scope/propose_plan" and calls[0].prompt_version == 1
    assert update["cost_usd"] == 0.0


def test_scope_invariants_name_each_violation(brief: Brief) -> None:
    good = ScopeProposal(
        plan=plan_from_brief(brief), rationale="r", assumptions=[], open_questions=[]
    )
    assert scope_invariants(good, brief, set()) is None
    bad = good.model_copy(
        update={
            "plan": good.plan.model_copy(
                update={
                    "model_name": "other",
                    "thresholds": good.plan.thresholds.model_copy(update={"auc_warn": 0.9}),
                }
            ),
            "rationale": "see [K:k_x]",
        }
    )
    message = scope_invariants(bad, brief, {"k_other"})
    assert message is not None
    assert "model_name" in message and "auc_warn" in message and "k_x" in message


def test_scope_invariants_check_bounds_and_policy(brief: Brief) -> None:
    plan = plan_from_brief(brief)
    loose = plan.feature_policy.model_copy(
        update={"max_features": plan.feature_policy.max_features + 1}
    )
    wild = plan.thresholds.model_copy(update={"psi_fail": 1.5, "eo_high": 0.9})
    bad = ScopeProposal(
        plan=plan.model_copy(update={"feature_policy": loose, "thresholds": wild}),
        rationale="cites [K:k_a]",
        assumptions=[],
        open_questions=[],
    )
    message = scope_invariants(bad, brief, {"k_a"})
    assert message is not None
    assert "max_features" in message and "psi_fail" in message and "eo_high" in message
    assert "k_a" not in message


@pytest.mark.db
def test_scope_agent_parks_on_persistent_invariant_violation(
    synthetic_state: RunState, runtime_with_model: RuntimeWithModel, db: Database, brief: Brief
) -> None:
    plan = plan_from_brief(brief).model_copy(update={"model_name": "renamed_pd"})
    proposal = ScopeProposal(plan=plan, rationale="r", assumptions=[], open_questions=[])
    runtime = runtime_with_model(StubModel(proposal=proposal))
    state = apply_update(synthetic_state, intake(synthetic_state, runtime))
    with pytest.raises(GatewayError) as caught:
        scope(state, runtime)
    assert caught.value.kind == "schema" and "model_name" in caught.value.message
    with db.connection() as conn:
        calls = list_llm_calls(conn, state.run_id)
    assert [c.schema_valid for c in calls] == [False, False, False]


@pytest.mark.db
def test_scope_replay_copies_plan_and_notes_but_records_index(
    db: Database, completed_run_id: str, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    # ``db`` truncates the tables, so it must come before ``completed_run_id``.
    state = make_run(db, store, SYNTHETIC_BRIEF, replay_of=completed_run_id)
    original = original_manifest(state, runtime.context)
    update = scope(state, runtime)
    assert update["plan"] == store.get_model(original.initial_plan_ref, type(update["plan"]))
    assert set(update["refs"]) == {"knowledge_index", "scope_notes"}
    assert update["refs"]["scope_notes"].id == original.refs["scope_notes"].id
    assert update["refs"]["scope_notes"].summary == original.refs["scope_notes"].summary
    assert update["refs"]["knowledge_index"].kind == "knowledge_index"
    with db.connection() as conn:
        assert list_llm_calls(conn, state.run_id) == []


def _restricted_brief(term: str, portfolio: str | None = None) -> bytes:
    text = SYNTHETIC_BRIEF.read_text()
    if portfolio is not None:
        text = "\n".join(
            f"portfolio: {portfolio}" if line.startswith("portfolio:") else line
            for line in text.splitlines()
        )
    return (text.rstrip("\n") + f"\nrestricted_terms: [{term}]\n").encode()


@pytest.mark.db
def test_scope_refuses_restricted_term_in_brief_body(
    db: Database, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    # The term also appears in the brief's `portfolio`, which the scope prompt carries.
    state = make_run(db, store, brief_bytes=_restricted_brief("offline demonstration"))
    state = apply_update(state, intake(state, runtime))
    with pytest.raises(GatewayError) as exc:
        scope(state, runtime)
    assert exc.value.kind == "redaction" and "restricted_term" in str(exc.value)


@pytest.mark.db
def test_scope_runs_when_the_term_appears_only_in_restricted_terms(
    db: Database, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    state = make_run(db, store, brief_bytes=_restricted_brief("platt"))
    state = apply_update(state, intake(state, runtime))
    update = scope(state, runtime)
    assert update["plan"].model_name == "synthetic_pd"
    # The `brief` artifact keeps the raw bytes, `restricted_terms` included.
    assert b"restricted_terms: [platt]" in store.get(state.brief_ref)


@pytest.mark.db
def test_scope_refuses_a_term_straddling_the_yaml_fold_column(
    db: Database, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    # "Acme" ends just before column 80 of the dumped `portfolio:` line, where a default
    # `yaml.safe_dump` folds the scalar and would split the term across two lines.
    portfolio = (
        "A long portfolio description that goes on and on for the purpose of Acme Holdings Ltd book"
    )
    assert (
        (len("portfolio: ") + portfolio.index("Acme Holdings"))
        < 80
        < (len("portfolio: ") + portfolio.index("Holdings") + len("Holdings"))
    )
    brief = _restricted_brief("Acme Holdings", portfolio)
    state = make_run(db, store, brief_bytes=brief)
    state = apply_update(state, intake(state, runtime))
    with pytest.raises(GatewayError) as exc:
        scope(state, runtime)
    assert exc.value.kind == "redaction" and "restricted_term" in str(exc.value)
