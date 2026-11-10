"""The documentation agent: query planning, grounded writing, post-checks, banner and render."""

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.runtime import Runtime
from modelforge_gateway.gateway import Gateway
from modelforge_gateway.profiles import Route, load_profile
from modelforge_gateway.prompts import PromptRegistry
from modelforge_gateway.schemas import GatewayError
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage.db import Database
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.store import MEDIA_JSON, MEDIA_YAML, ArtifactStore
from modelforge_mcp.schemas import (
    CompletenessReport,
    DocumentSection,
    DocumentSections,
    GroundingReport,
)
from modelforge_mcp.servers.doc_renderer import (
    BANNER,
    CheckCompletenessInput,
    RenderDocumentInput,
    RenderOutput,
)
from modelforge_orchestrator.agent_schemas import QueryPlan, SectionDraft
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.grounding import check_section
from modelforge_orchestrator.nodes.document import _bannered, document
from modelforge_orchestrator.nodes.gates import gate_summary
from modelforge_orchestrator.schemas import RunState
from pydantic import BaseModel

from .conftest import REPO, RuntimeWithFaults, RuntimeWithModel


class DraftModel(BaseChatModel):
    """Plans one query and writes ``body`` for every section."""

    body: str

    @property
    def _llm_type(self) -> str:
        return "draft-stub"

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
        parsed: BaseModel = (
            QueryPlan(queries=["data"]) if schema is QueryPlan else SectionDraft(body_md=self.body)
        )

        def respond(model_input: LanguageModelInput) -> Any:
            raw = AIMessage(
                content=parsed.model_dump_json(),
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"stop_reason": "end_turn"},
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return RunnableLambda(respond)


@pytest.mark.db
def test_document_writes_grounded_sections_and_renders(
    validated_state: RunState, deps: Deps, runtime: Runtime[Deps], db: Database
) -> None:
    update = document(validated_state, runtime)
    refs = update["refs"]
    sections = deps.store.get_model(refs["document_sections"].id, DocumentSections)
    template = yaml.safe_load((REPO / "templates/sr11-7-dev-doc/sections.yaml").read_text())
    assert [s.section_id for s in sections.sections] == [e["id"] for e in template]
    assert all(s.grounding is not None and s.grounding.ok for s in sections.sections)
    assert refs["document_sections"].summary == {
        "n_sections": 11,
        "n_flagged_sections": 0,
        "n_ungrounded_numbers": 0,
        "n_llm_calls": 22,
    }
    completeness = refs["completeness"].summary
    assert completeness["missing_sections"] == []
    assert completeness["n_uncited"] == 0 and completeness["n_unresolved_markers"] == 0
    perf = next(s for s in sections.sections if s.section_id == "performance").body_md
    assert f"[A:{validated_state.refs['discrimination'].id}#by_split.oot.auc]" in perf
    assert "[A:discrimination#" not in perf
    with db.connection() as conn:
        calls = list_llm_calls(conn, validated_state.run_id)
    # The run also holds the scope agent's call; the document node made 22.
    assert len([c for c in calls if c.purpose == "document"]) == 22


@pytest.mark.db
def test_document_regenerates_then_banners_ungrounded_section(
    validated_state: RunState, runtime_with_faults: RuntimeWithFaults
) -> None:
    runtime = runtime_with_faults({"data": "ungrounded"})
    update = document(validated_state, runtime)
    ref = update["refs"]["document_sections"]
    sections = runtime.context.store.get_model(ref.id, DocumentSections)
    data = next(s for s in sections.sections if s.section_id == "data")
    assert data.body_md.startswith(BANNER)
    assert data.grounding is not None and data.grounding.ungrounded_numbers == ["0.1234"]
    assert ref.summary["n_flagged_sections"] == 1
    assert ref.summary["n_llm_calls"] == 24


@pytest.mark.db
def test_document_parks_on_refusal(
    validated_state: RunState, runtime_with_faults: RuntimeWithFaults
) -> None:
    with pytest.raises(GatewayError) as exc:
        document(validated_state, runtime_with_faults({"data": "refuse"}))
    assert exc.value.kind == "refusal"


@pytest.mark.db
def test_document_refuses_restricted_term_in_excerpt(
    validated_state: RunState, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    # The validated run is pointed at a brief restricting `platt` for `document` alone (the
    # scope prompt omits `restricted_terms`, so such a brief also passes `scope`; see
    # test_scope_agent). The development section's calibration excerpt carries "platt".
    text = (REPO / "examples/briefs/synthetic.yaml").read_text()
    brief = store.put(
        (text.rstrip("\n") + "\nrestricted_terms: [platt]\n").encode(),
        kind="brief",
        media_type=MEDIA_YAML,
    )
    state = validated_state.model_copy(update={"brief_ref": brief.id})
    with pytest.raises(GatewayError) as exc:
        document(state, runtime)
    assert exc.value.kind == "redaction"
    assert "restricted_term" in str(exc.value) and "platt" not in str(exc.value)


@pytest.mark.db
def test_signoff_summary_includes_document(signed_off_state: RunState, deps: Deps) -> None:
    summary = gate_summary("signoff", signed_off_state, deps.store)
    assert summary["document"]["n_flagged_sections"] == 0


@pytest.mark.db
def test_grounding_sees_the_excerpt_not_the_hidden_bulk_list(
    validated_state: RunState, store: ArtifactStore, runtime_with_model: RuntimeWithModel
) -> None:
    # Digit-only row ids, so the hidden list itself holds small integers.
    split = json.loads(store.get(validated_state.refs["split"].id))
    train = [str(1000 + i) for i in range(len(split["row_ids"]["train"]))]
    split["row_ids"]["train"] = train
    ref = store.put(json.dumps(split).encode(), kind="split_manifest", media_type=MEDIA_JSON)
    state = validated_state.model_copy(update={"refs": {**validated_state.refs, "split": ref}})
    small, count = train[1], len(train)
    body = (
        f"The training split lists {small} [A:split#row_ids.train].\n\n"
        f"The training split holds {count} rows [A:split#row_ids.train.count]."
    )
    runtime = runtime_with_model(DraftModel(body=body))
    update = document(state, runtime)
    sections = store.get_model(update["refs"]["document_sections"].id, DocumentSections)
    data = next(s for s in sections.sections if s.section_id == "data")
    assert data.grounding is not None
    assert data.grounding.ungrounded_numbers == [small]
    assert data.grounding.unresolved_markers == []


def test_banner_failures_do_not_reenter_the_rendered_document(
    deps: Deps, store: ArtifactStore
) -> None:
    report = GroundingReport(
        ungrounded_numbers=["0.1234"],
        unresolved_markers=["[A:nope#auc]", "[K:]"],
        uncited_regulatory_sentences=["Under SR 11-7 and `Basel` the model is reviewed."],
        foreign_passages=["[K:k_elsewhere]"],
        ok=False,
    )
    body = _bannered("The section text is clean.", report)
    assert "`[A:nope#auc]`" in body and "'Basel'" in body
    # The grounding checker reads no number in the banner's items.
    rechecked = check_section(body, load_artifact=lambda _: {}, retrieved=set())
    assert rechecked.ungrounded_numbers == []
    sections = DocumentSections(
        template_id="sr11-7-dev-doc",
        sections=[DocumentSection(section_id="data", body_md=body)],
    )
    sections_id = store.put_model(sections, kind="document_sections").id
    rendered: RenderOutput = deps.tools.call(
        "doc-renderer",
        "render_document",
        RenderDocumentInput(template_id="sr11-7-dev-doc", sections_id=sections_id, title="t"),
    ).output
    completeness = deps.tools.call(
        "doc-renderer",
        "check_completeness",
        CheckCompletenessInput(document_id=rendered.markdown.id, template_id="sr11-7-dev-doc"),
    ).refs[0]
    assert completeness.summary is not None
    assert completeness.summary["n_unresolved_markers"] == 0
    assert completeness.summary["n_uncited"] == 0
    assert store.get_model(completeness.id, CompletenessReport).unresolved_markers == []


@pytest.mark.db
def test_document_requests_room_for_planning_and_writing(
    validated_state: RunState, deps: Deps
) -> None:
    budgets: list[int] = []
    model = DraftModel(body="The section text is clean.")

    def factory(route: Route, settings: GatewaySettings, max_output_tokens: int) -> BaseChatModel:
        budgets.append(max_output_tokens)
        return model

    gateway_settings = deps.settings.gateway
    gateway = Gateway(
        gateway_settings,
        load_profile(gateway_settings.profiles_dir, "fake"),
        deps.lineage,
        deps.store,
        PromptRegistry(gateway_settings.prompts_dir, deps.lineage),
        chat_model_factory=factory,
    )
    context = Deps(
        tools=deps.tools,
        lineage=deps.lineage,
        store=deps.store,
        settings=deps.settings,
        gateway=gateway,
    )
    document(validated_state, cast("Runtime[Deps]", SimpleNamespace(context=context)))
    # Per section: a planning call, then a writing call.
    assert budgets[:2] == [1024, 8192]
    assert set(budgets[0::2]) == {1024} and set(budgets[1::2]) == {8192}
