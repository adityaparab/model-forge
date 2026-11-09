"""Placeholder; replaced in Task 17."""

from typing import Any

from langgraph.runtime import Runtime

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import RunState


def seal(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:  # replaced in Task 17
    del runtime
    return {"status": state.status}
