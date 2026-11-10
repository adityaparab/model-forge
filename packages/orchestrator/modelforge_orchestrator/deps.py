"""What every graph node receives: tools, the model gateway, lineage, the store, settings and
the trace context of the run."""

from dataclasses import dataclass

from modelforge_gateway.gateway import Gateway
from modelforge_lineage.facade import Lineage
from modelforge_lineage.store import ArtifactStore
from opentelemetry.context import Context

from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.tools import ToolClient


@dataclass
class Deps:
    tools: ToolClient
    lineage: Lineage
    store: ArtifactStore
    settings: Settings
    gateway: Gateway
    trace_context: Context | None = None
    """The ``run.execute`` span's context: the parent of a node span when none is current."""
