"""The run's LangGraph state machine: every node, the fixed edges, and the gate routing."""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.build import build
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.features import features
from modelforge_orchestrator.nodes.gates import gate_model, gate_scope, gate_signoff
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.profile import profile
from modelforge_orchestrator.nodes.remediate import remediate
from modelforge_orchestrator.nodes.scope import scope
from modelforge_orchestrator.nodes.seal import seal
from modelforge_orchestrator.nodes.split import split
from modelforge_orchestrator.nodes.validate import validate
from modelforge_orchestrator.schemas import RunState

__all__ = ["NODES", "build_graph"]

NODES: dict[str, Callable[..., Any]] = {
    "intake": intake,
    "scope": scope,
    "gate_scope": gate_scope,
    "profile": profile,
    "split": split,
    "features": features,
    "build": build,
    "validate": validate,
    "gate_model": gate_model,
    "document": document,
    "challenge": challenge,
    "remediate": remediate,
    "gate_signoff": gate_signoff,
    "seal": seal,
}

_EDGES: tuple[tuple[str, str], ...] = (
    (START, "intake"),
    ("intake", "scope"),
    ("scope", "gate_scope"),
    ("profile", "split"),
    ("split", "features"),
    ("features", "build"),
    ("build", "validate"),
    ("validate", "gate_model"),
    ("document", "challenge"),
    ("remediate", "challenge"),
    ("gate_signoff", "seal"),
    ("seal", END),
)
"""Fixed edges; the gates and `challenge` route through `Command`, and `remediate` returns a
`Command` back to `challenge` (the same target as its edge)."""


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> CompiledStateGraph[Any, Any, Any, Any]:
    builder = StateGraph(RunState, context_schema=Deps)
    for name, node in NODES.items():
        builder.add_node(name, node)
    for source, target in _EDGES:
        builder.add_edge(source, target)
    return builder.compile(checkpointer=checkpointer)
