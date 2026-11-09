"""What every graph node receives: the tool client, lineage, the artifact store and settings."""

from dataclasses import dataclass

from modelforge_lineage.facade import Lineage
from modelforge_lineage.store import ArtifactStore

from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.tools import ToolClient


@dataclass
class Deps:
    tools: ToolClient
    lineage: Lineage
    store: ArtifactStore
    settings: Settings
