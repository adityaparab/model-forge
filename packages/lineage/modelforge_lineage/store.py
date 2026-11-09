"""Content-addressed filesystem artifact store with atomic writes."""

import io
import os
import uuid
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from modelforge_lineage.canonical import sha256_hex
from modelforge_lineage.models import ArtifactRef, Producer

MEDIA_JSON = "application/json"
MEDIA_PARQUET = "application/vnd.apache.parquet"
MEDIA_MARKDOWN = "text/markdown"
MEDIA_HTML = "text/html"
MEDIA_YAML = "application/yaml"

T = TypeVar("T", bound=BaseModel)


class ArtifactNotFound(KeyError):  # noqa: N818 - name fixed by the lineage interface
    pass


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, artifact_id: str) -> Path:
        return self.root / artifact_id[:2] / artifact_id[2:4] / artifact_id

    def uri(self, artifact_id: str) -> str:
        return self.path(artifact_id).resolve().as_uri()

    def exists(self, artifact_id: str) -> bool:
        return self.path(artifact_id).is_file()

    def put(
        self,
        data: bytes,
        *,
        kind: str,
        media_type: str,
        producer: Producer | None = None,
        summary: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        artifact_id = sha256_hex(data)
        final_path = self.path(artifact_id)
        if final_path.exists():
            size = final_path.stat().st_size
        else:
            tmp_dir = self.root / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / uuid.uuid4().hex
            try:
                tmp_path.write_bytes(data)
                os.replace(tmp_path, final_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            size = len(data)
        return ArtifactRef(
            id=artifact_id,
            kind=kind,
            media_type=media_type,
            size_bytes=size,
            producer=producer,
            summary=summary if summary is not None else {},
        )

    def get(self, artifact_id: str) -> bytes:
        try:
            return self.path(artifact_id).read_bytes()
        except FileNotFoundError:
            raise ArtifactNotFound(artifact_id) from None

    def put_model(
        self,
        model: BaseModel,
        *,
        kind: str,
        producer: Producer | None = None,
        summary: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        data = model.model_dump_json(indent=2).encode()
        return self.put(data, kind=kind, media_type=MEDIA_JSON, producer=producer, summary=summary)

    def get_model(self, artifact_id: str, model_type: type[T]) -> T:
        return model_type.model_validate_json(self.get(artifact_id))

    def put_frame(
        self,
        frame: pd.DataFrame,
        *,
        kind: str,
        producer: Producer | None = None,
        summary: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        sink = io.BytesIO()
        pq.write_table(table, sink, compression="zstd")
        return self.put(
            sink.getvalue(), kind=kind, media_type=MEDIA_PARQUET, producer=producer, summary=summary
        )

    def get_frame(self, artifact_id: str) -> pd.DataFrame:
        return pq.read_table(io.BytesIO(self.get(artifact_id))).to_pandas()
