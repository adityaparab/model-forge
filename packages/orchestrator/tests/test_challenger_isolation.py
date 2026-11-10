"""The challenger's context is built from lineage only: developer state never reaches a prompt."""

import json

import pytest
from langgraph.runtime import Runtime
from modelforge_core.schemas import ColumnDictionary
from modelforge_lineage import steps
from modelforge_lineage.db import Database
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.store import MEDIA_JSON, ArtifactStore
from modelforge_mcp.schemas import DocumentSections
from modelforge_mcp.servers.doc_renderer import MARKER
from modelforge_orchestrator.agent_schemas import ScopeNotes
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.build import build
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.features import features
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.profile import profile
from modelforge_orchestrator.nodes.scope import scope
from modelforge_orchestrator.nodes.split import split
from modelforge_orchestrator.nodes.validate import validate
from modelforge_orchestrator.schemas import RunState
from pydantic import BaseModel

from .conftest import SYNTHETIC_BRIEF, apply_node, make_run, recording_runtime

SENTINELS = {
    "brief": "SENTINEL-BRIEF-7f3a",
    "scope": "SENTINEL-SCOPE-9c1d",
    "draft": "SENTINEL-DRAFT-2b8e",
    "raw": "SENTINEL-RAW-5d4f",
    "dictionary": "SENTINEL-DICT-1a9b",
}
ALLOWED_KINDS = {
    "document_md",
    "completeness",
    "validation_report",
    "modeling_plan",
    "knowledge_index",
    "brief",
    "column_dictionary",
}
"""What the challenger reads: its context, plus the brief and dictionary for the redaction list."""


class RecordingStore(ArtifactStore):
    """An `ArtifactStore` logging the id of every artifact read."""

    read_log: list[str]

    def get(self, artifact_id: str) -> bytes:
        self.read_log.append(artifact_id)
        return super().get(artifact_id)


def _record(db: Database, state: RunState, store: ArtifactStore, ref: ArtifactRef) -> None:
    with db.transaction() as conn:
        steps.insert_artifact(
            conn, run_id=state.run_id, ref=ref, uri=store.uri(ref.id), producer_step_id=None
        )


def _plant(
    db: Database, store: ArtifactStore, state: RunState, model: BaseModel, kind: str
) -> ArtifactRef:
    ref = store.put_model(model, kind=kind)
    _record(db, state, store, ref)
    return ref


def _cited_ids(markdown: str) -> set[str]:
    return {m.group(2).partition("#")[0] for m in MARKER.finditer(markdown) if m.group(1) == "A"}


@pytest.mark.db
def test_developer_state_cannot_reach_the_challenger(
    db: Database, store: ArtifactStore, deps: Deps, runtime: Runtime[Deps]
) -> None:
    text = SYNTHETIC_BRIEF.read_text().rstrip("\n")
    brief = f'{text}\nconstraints: "{SENTINELS["brief"]}"\n'.encode()
    state = make_run(db, store, brief_bytes=brief)
    for node in (intake, scope, profile, split, features, build, validate, document):
        state = apply_node(state, node(state, runtime))

    notes = store.get_model(state.refs["scope_notes"].id, ScopeNotes)
    notes = notes.model_copy(
        update={
            "rationale": f"{notes.rationale} {SENTINELS['scope']}",
            "open_questions": [SENTINELS["scope"]],
        }
    )
    sections = store.get_model(state.refs["document_sections"].id, DocumentSections)
    first = sections.sections[0]
    assert first.grounding is not None
    superseded = first.model_copy(
        update={
            "body_md": f"{first.body_md} {SENTINELS['draft']}",
            "grounding": first.grounding.model_copy(
                update={"ungrounded_numbers": [SENTINELS["draft"]]}
            ),
        }
    )
    drafts = sections.model_copy(update={"sections": [superseded, *sections.sections[1:]]})
    dictionary = store.get_model(state.refs["dictionary"].id, ColumnDictionary)
    column = dictionary.columns[0].model_copy(update={"description": SENTINELS["dictionary"]})
    dictionary = dictionary.model_copy(update={"columns": [column, *dictionary.columns[1:]]})
    raw = store.put(
        json.dumps({"text": SENTINELS["raw"]}).encode(), kind="llm_response", media_type=MEDIA_JSON
    )
    _record(db, state, store, raw)
    state = state.model_copy(
        update={
            "refs": {
                **state.refs,
                "scope_notes": _plant(db, store, state, notes, "scope_notes"),
                "document_sections": _plant(db, store, state, drafts, "document_sections"),
                "dictionary": _plant(db, store, state, dictionary, "column_dictionary"),
            }
        }
    )

    recording_store = RecordingStore(store.root)
    recording_store.read_log = []
    recorded = Deps(
        tools=deps.tools,
        lineage=deps.lineage,
        store=recording_store,
        settings=deps.settings,
        gateway=deps.gateway,
    )
    recording, captured = recording_runtime(recorded)
    challenge(state, recording)

    assert captured, "the challenger made no call"
    sent = "\n".join(str(m.content) for messages in captured for m in messages)
    markdown = store.get(state.refs["document_md"].id).decode()
    assert markdown.splitlines()[0] in sent  # the document did reach the model
    for name, sentinel in SENTINELS.items():
        assert sentinel not in sent, name

    with db.connection() as conn:
        kinds = {row.id: row.kind for row in steps.list_artifacts(conn, state.run_id)}
    kinds[state.brief_ref] = "brief"
    cited = _cited_ids(markdown)
    reads = [(artifact_id, kinds.get(artifact_id)) for artifact_id in recording_store.read_log]
    assert reads
    outside = [(i, k) for i, k in reads if k not in ALLOWED_KINDS and i not in cited]
    assert outside == []
    assert state.refs["scope_notes"].id not in recording_store.read_log
    assert state.refs["document_sections"].id not in recording_store.read_log
    assert raw.id not in recording_store.read_log
