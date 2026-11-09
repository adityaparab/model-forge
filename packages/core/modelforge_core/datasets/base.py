"""Canonical dataset contract: adapter protocol, column sanitisation, validation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pandas as pd

from modelforge_core.schemas import FEATURE_PATTERN, ID_COLUMNS, ColumnDictionary, ColumnEntry


class CanonicalSchemaError(ValueError):
    """Raised when a frame does not satisfy the canonical dataset contract."""


@dataclass(frozen=True)
class LoadedDataset:
    name: str
    frame: pd.DataFrame
    dictionary: ColumnDictionary
    oot_capable: bool


class DatasetAdapter(Protocol):
    name: str

    def load(self, cache_dir: Path) -> LoadedDataset: ...


def _require_id_columns(frame: pd.DataFrame) -> None:
    for col in ID_COLUMNS:
        if col not in frame.columns:
            raise CanonicalSchemaError(f"missing ID column {col!r}")


def sanitize_columns(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    *,
    dataset: str,
    descriptions: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, ColumnDictionary]:
    """Rename features to f_1..f_n (float64), keep ID columns, drop everything else."""
    _require_id_columns(frame)
    frame = frame.reset_index(drop=True)
    columns: dict[str, pd.Series] = {col: cast(pd.Series, frame[col]) for col in ID_COLUMNS}
    entries: list[ColumnEntry] = []
    for i, original in enumerate(feature_columns, start=1):
        name = f"f_{i}"
        numeric = cast(pd.Series, pd.to_numeric(frame[original], errors="raise"))
        columns[name] = numeric.astype("float64")
        description = descriptions.get(original) if descriptions is not None else None
        entries.append(ColumnEntry(name=name, original=original, description=description))
    out = pd.DataFrame(columns)
    return out, ColumnDictionary(dataset=dataset, columns=entries)


def validate_canonical(frame: pd.DataFrame) -> None:
    """Raise CanonicalSchemaError unless the frame satisfies the canonical contract."""
    _require_id_columns(frame)
    if not pd.api.types.is_integer_dtype(frame["vintage"]):
        raise CanonicalSchemaError(f"'vintage' must be integer dtype, got {frame['vintage'].dtype}")
    bad_flags = sorted(set(frame["default_flag"].unique()) - {0, 1}, key=str)
    if bad_flags:
        raise CanonicalSchemaError(f"'default_flag' has values outside {{0, 1}}: {bad_flags}")
    for col in frame.columns:
        if col in ID_COLUMNS:
            continue
        if not FEATURE_PATTERN.match(str(col)):
            raise CanonicalSchemaError(f"column {col!r} is not an ID column and not named f_<n>")
        if not pd.api.types.is_numeric_dtype(frame[col]):
            raise CanonicalSchemaError(
                f"feature column {col!r} is not numeric (dtype {frame[col].dtype})"
            )
    ids = cast(pd.Series, frame["obligor_id"])
    duplicated = sorted(set(ids[ids.duplicated()].tolist()), key=str)
    if duplicated:
        raise CanonicalSchemaError(f"duplicate 'obligor_id' values: {duplicated[:10]}")
