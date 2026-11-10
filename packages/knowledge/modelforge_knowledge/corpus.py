"""Load the Markdown knowledge corpus: YAML front-matter plus body text."""

import hashlib
from pathlib import Path
from typing import Literal, get_args

import yaml
from modelforge_core.schemas import StrictModel

DocumentKind = Literal["primary", "summary", "internal"]

REQUIRED_KEYS = ("id", "title", "source", "authority", "date", "url", "kind")
_KINDS: tuple[str, ...] = get_args(DocumentKind)


class CorpusError(ValueError):
    """A corpus file is missing, malformed or inconsistent."""


class Document(StrictModel):
    id: str
    title: str
    source: str
    authority: str
    date: str
    url: str
    kind: DocumentKind
    text: str
    sha256: str


def _parse(path: Path) -> Document:
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorpusError(f"{path.name}: not valid UTF-8") from exc
    lines = content.splitlines(keepends=True)
    fences = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == "---"][:2]
    if len(fences) < 2 or fences[0] != 0:
        raise CorpusError(f"{path.name}: no front-matter between two '---' lines")
    start, end = fences
    try:
        meta = yaml.safe_load("".join(lines[start + 1 : end]))
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path.name}: front-matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise CorpusError(f"{path.name}: front-matter is not a mapping")
    missing = [key for key in REQUIRED_KEYS if key not in meta]
    if missing:
        raise CorpusError(f"{path.name}: front-matter is missing {', '.join(missing)}")
    fields = {key: meta[key] for key in REQUIRED_KEYS}
    not_str = [key for key, value in fields.items() if not isinstance(value, str)]
    if not_str:
        raise CorpusError(f"{path.name}: front-matter values must be strings: {', '.join(not_str)}")
    if fields["id"] != path.stem:
        raise CorpusError(f"{path.name}: id {fields['id']!r} does not match the file stem")
    if fields["kind"] not in _KINDS:
        raise CorpusError(f"{path.name}: kind {fields['kind']!r} is not one of {', '.join(_KINDS)}")
    text = "".join(lines[end + 1 :])
    if text.startswith("\r\n"):
        text = text[2:]
    elif text.startswith("\n"):
        text = text[1:]
    return Document(**fields, text=text, sha256=hashlib.sha256(raw).hexdigest())


def load_corpus(knowledge_dir: Path) -> list[Document]:
    """Every ``*.md`` under ``knowledge_dir`` (not recursive), sorted by file name."""
    paths = sorted(knowledge_dir.glob("*.md"), key=lambda p: p.name)
    if not paths:
        raise CorpusError(f"{knowledge_dir}: no *.md documents found")
    docs: list[Document] = []
    seen: dict[str, str] = {}
    for path in paths:
        doc = _parse(path)
        if doc.id in seen:
            raise CorpusError(f"{path.name}: duplicate id {doc.id!r} (also in {seen[doc.id]})")
        seen[doc.id] = path.name
        docs.append(doc)
    return docs
