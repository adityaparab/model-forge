"""Versioned prompt templates rendered for gateway calls."""

import re
from pathlib import Path
from typing import Any

import jinja2
import yaml
from modelforge_core.schemas import StrictModel
from modelforge_lineage.canonical import sha256_hex
from modelforge_lineage.facade import Lineage
from modelforge_lineage.models import PromptRow
from modelforge_lineage.prompts import PromptHashConflict, upsert_prompt
from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from modelforge_gateway.schemas import GatewayError, Purpose

PROMPT_NAME_PATTERN = re.compile(r"^[a-z0-9_]+/[a-z0-9_]+$")
FRONT_MATTER_DELIMITER = "---"


class PromptRecord(StrictModel):
    name: str
    version: int
    path: str
    hash: str
    purpose: Purpose
    output_schema: str
    description: str
    template: str


class _FrontMatter(BaseModel):
    """The required front-matter keys; any other key is ignored."""

    model_config = ConfigDict(extra="ignore")

    purpose: Purpose
    version: StrictInt
    output_schema: str
    description: str


def _split_front_matter(text: str, path: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    delimiters = [
        i for i, line in enumerate(lines) if line.rstrip("\r\n") == FRONT_MATTER_DELIMITER
    ][:2]
    if len(delimiters) < 2:
        raise GatewayError("config", f"prompt {path} has no front-matter between --- lines")
    start, end = delimiters
    if any(line.strip() for line in lines[:start]):
        raise GatewayError("config", f"prompt {path} has text before its front-matter")
    try:
        meta = yaml.safe_load("".join(lines[start + 1 : end]))
    except yaml.YAMLError as exc:
        raise GatewayError("config", f"prompt {path} has invalid front-matter: {exc}") from exc
    if not isinstance(meta, dict):
        raise GatewayError("config", f"prompt {path} front-matter is not a mapping")
    return meta, "".join(lines[end + 1 :])


class PromptRegistry:
    def __init__(self, prompts_dir: Path, lineage: Lineage | None = None) -> None:
        self.prompts_dir = prompts_dir
        self.lineage = lineage
        self._records: dict[tuple[str, int], PromptRecord] = {}
        self._env = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def get(self, name: str, version: int) -> PromptRecord:
        """Load ``<prompts_dir>/<name>.v<version>.md``, recording it in lineage once."""
        key = (name, version)
        cached = self._records.get(key)
        if cached is not None:
            return cached
        record = self._load(name, version)
        if self.lineage is not None:
            row = PromptRow(
                name=record.name, version=record.version, hash=record.hash, path=record.path
            )
            try:
                with self.lineage.transaction() as conn:
                    upsert_prompt(conn, row)
            except PromptHashConflict as exc:
                raise GatewayError(
                    "config", f"prompt {record.path} changed without a new version: {exc}"
                ) from exc
        self._records[key] = record
        return record

    def render(self, record: PromptRecord, **values: Any) -> str:
        """Render ``record``'s template; an undefined variable is a ``config`` error."""
        try:
            return self._env.from_string(record.template).render(**values)
        except (jinja2.UndefinedError, jinja2.TemplateSyntaxError) as exc:
            raise GatewayError("config", f"prompt {record.path} failed to render: {exc}") from exc

    def _load(self, name: str, version: int) -> PromptRecord:
        if not PROMPT_NAME_PATTERN.match(name):
            raise GatewayError("config", f"invalid prompt name {name!r}")
        relative = f"{name}.v{version}.md"
        file = self.prompts_dir / relative
        if not file.is_file():
            raise GatewayError("config", f"prompt {relative} not found in {self.prompts_dir}")
        data = file.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GatewayError("config", f"prompt {relative} is not UTF-8: {exc}") from exc
        meta, template = _split_front_matter(text, relative)
        try:
            front = _FrontMatter.model_validate(meta)
        except ValidationError as exc:
            raise GatewayError(
                "config", f"prompt {relative} has invalid front-matter: {exc}"
            ) from exc
        if front.version != version:
            raise GatewayError(
                "config", f"prompt {relative} declares version {front.version}, not {version}"
            )
        if not front.output_schema.strip() or not front.description.strip():
            raise GatewayError(
                "config", f"prompt {relative} needs non-empty output_schema and description"
            )
        return PromptRecord(
            name=name,
            version=version,
            path=relative,
            hash=sha256_hex(data),
            purpose=front.purpose,
            output_schema=front.output_schema,
            description=front.description,
            template=template,
        )
