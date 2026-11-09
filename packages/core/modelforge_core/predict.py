"""Parse a serialised model and score with it, whichever kind it is."""

import json

import numpy as np
import pandas as pd
from pydantic import ValidationError

from modelforge_core.gbm import predict_gbm
from modelforge_core.schemas import GBMModel, Scorecard
from modelforge_core.scorecard import predict_pd


def parse_model(text: str) -> Scorecard | GBMModel:
    """Validate JSON as a GBMModel (has ``model_text``) or a Scorecard; ValueError otherwise."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model text is not valid JSON: {exc}") from None
    kind: type[Scorecard] | type[GBMModel] = (
        GBMModel if isinstance(data, dict) and "model_text" in data else Scorecard
    )
    try:
        return kind.model_validate_json(text)
    except ValidationError as exc:
        raise ValueError(
            f"model text is neither a valid Scorecard nor a valid GBMModel "
            f"({kind.__name__} validation failed: {exc.error_count()} errors)"
        ) from None


def predict_model(model: Scorecard | GBMModel, frame: pd.DataFrame) -> np.ndarray:
    """Raw PD scores from either model kind."""
    if isinstance(model, Scorecard):
        return predict_pd(model, frame)
    return predict_gbm(model, frame)
