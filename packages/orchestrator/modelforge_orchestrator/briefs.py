"""The modeling brief a user submits, and its mapping to a `ModelingPlan`."""

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from modelforge_core.schemas import (
    FeaturePolicy,
    Methodology,
    ModelingPlan,
    StrictModel,
    TargetDefinition,
    Thresholds,
    VintagePolicy,
)
from modelforge_core.standards import DEFAULT_THRESHOLDS
from modelforge_mcp.schemas import DatasetRef
from pydantic import Field, ValidationError


class Brief(StrictModel):
    model_name: str
    portfolio: str
    dataset: DatasetRef
    target: TargetDefinition
    intended_use: str
    regulatory_scope: list[str]
    tier: Literal[1, 2, 3]
    vintage_policy: VintagePolicy
    feature_policy: FeaturePolicy = Field(default_factory=FeaturePolicy)
    thresholds: dict[str, float] = Field(default_factory=dict)
    constraints: str = ""
    restricted_terms: list[str] = Field(default_factory=list)


class BriefError(ValueError):
    """The brief is not valid YAML or does not match the `Brief` schema."""


def parse_brief(data: bytes) -> Brief:
    try:
        document = yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise BriefError(f"brief is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise BriefError("brief must be a YAML mapping")
    try:
        return Brief.model_validate(document)
    except ValidationError as exc:
        raise BriefError(str(exc)) from exc


def load_brief(path: Path) -> tuple[Brief, bytes]:
    """Parse the brief at ``path``; also return its bytes, which become the `brief` artifact."""
    data = path.read_bytes()
    return parse_brief(data), data


def brief_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_from_brief(brief: Brief) -> ModelingPlan:
    """The initial plan: brief fields copied, default methodology, thresholds with overrides."""
    # Validate the merged mapping: `model_copy(update=...)` alone would accept unknown keys.
    thresholds = Thresholds.model_validate({**DEFAULT_THRESHOLDS.model_dump(), **brief.thresholds})
    return ModelingPlan(
        model_name=brief.model_name,
        portfolio=brief.portfolio,
        target=brief.target,
        vintage_policy=brief.vintage_policy,
        feature_policy=brief.feature_policy,
        methodology=Methodology(),
        thresholds=thresholds,
        regulatory_scope=brief.regulatory_scope,
        intended_use=brief.intended_use,
        tier=brief.tier,
    )
