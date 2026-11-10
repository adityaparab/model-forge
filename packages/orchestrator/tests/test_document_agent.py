"""The documentation agent: query planning, grounded writing, post-checks, banner and render."""

import pytest
import yaml
from langgraph.runtime import Runtime
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage.db import Database
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_mcp.schemas import DocumentSections
from modelforge_mcp.servers.doc_renderer import BANNER
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.gates import gate_summary
from modelforge_orchestrator.schemas import RunState

from .conftest import REPO, RuntimeWithFaults


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
    # The restricted brief cannot drive `scope` itself: the brief YAML in the scope prompt
    # carries the term. So the validated run is pointed at it for `document` alone, whose
    # development section's calibration excerpt carries `"method": "platt"`.
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
