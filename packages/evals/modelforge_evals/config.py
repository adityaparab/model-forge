"""The evals configuration (``evals/evals.yml``): briefs, scripted approvals and profiles."""

from pathlib import Path
from typing import Literal

import yaml
from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import GateName
from pydantic import Field, ValidationError, model_validator

Layer = Literal["retrieval", "pipeline", "challenger", "cost"]


class EvalConfigError(ValueError):
    """The evals configuration is not valid YAML or does not match `EvalsConfig`."""


class ApprovalSpec(StrictModel):
    gate: GateName
    decision: str
    approver: str


class ProfileEvalSpec(StrictModel):
    brief: str
    """A key of `EvalsConfig.briefs`."""
    layers: list[Layer]
    compare: Literal["exact", "tolerance"] = "tolerance"
    ignore: list[str] = Field(default_factory=list)
    """fnmatch globs of metric names `compare` skips under ``exact``."""
    tolerances: dict[str, float] = Field(default_factory=dict)
    """Negative: the metric may fall by at most this much; positive: rise by at most this much."""
    budgets: dict[str, float] = Field(default_factory=dict)
    """Absolute upper bounds."""


class EvalsConfig(StrictModel):
    briefs: dict[str, Path]
    approvals: list[ApprovalSpec]
    profiles: dict[str, ProfileEvalSpec]

    @model_validator(mode="after")
    def _profiles_name_known_briefs(self) -> "EvalsConfig":
        for name, spec in self.profiles.items():
            if spec.brief not in self.briefs:
                raise ValueError(f"profile {name!r} names unknown brief {spec.brief!r}")
        return self


def load_config(path: Path) -> EvalsConfig:
    """Load ``path``; brief paths resolve against the repository root, its parent's parent."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvalConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise EvalConfigError(f"{path} must be a YAML mapping")
    try:
        config = EvalsConfig.model_validate(document)
    except ValidationError as exc:
        raise EvalConfigError(f"{path}: {exc}") from exc
    root = path.resolve().parent.parent
    briefs = {key: root / brief for key, brief in config.briefs.items()}
    return config.model_copy(update={"briefs": briefs})
