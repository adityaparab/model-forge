"""Value types shared by the MCP servers and their clients."""

from typing import Literal

from modelforge_core.schemas import StrictModel
from modelforge_core.standards import DEFAULT_SEED


class DatasetRef(StrictModel):
    """A dataset adapter name plus the seed it is loaded with, written `adapter[@seed]`."""

    adapter: str
    seed: int = DEFAULT_SEED

    def as_string(self) -> str:
        return self.adapter if self.seed == DEFAULT_SEED else f"{self.adapter}@{self.seed}"

    @classmethod
    def parse(cls, text: str) -> "DatasetRef":
        adapter, sep, seed = text.partition("@")
        if not sep:
            return cls(adapter=adapter)
        try:
            return cls(adapter=adapter, seed=int(seed))
        except ValueError:
            raise ValueError(
                f"invalid dataset ref {text!r}; expected 'adapter' or 'adapter@seed'"
            ) from None


class SectionSpec(StrictModel):
    """One section of a document template: heading, whether required, and its source artifacts."""

    id: str
    title: str
    required: bool
    artifact_kinds: list[str]
    guidance: str


class TemplateSections(StrictModel):
    template_id: str
    sections: list[SectionSpec]


class Citation(StrictModel):
    """A citation marker: `[A:<artifact_id>#<path>]` or `[K:<citation_id>]`."""

    marker: str
    kind: Literal["A", "K"]
    target: str


class DocumentSection(StrictModel):
    section_id: str
    body_md: str
    citations: list[Citation] = []


class DocumentSections(StrictModel):
    """Section bodies written by the documentation agent, keyed by template section id."""

    template_id: str
    sections: list[DocumentSection]


class CompletenessReport(StrictModel):
    template_id: str
    document_id: str
    missing_sections: list[str]
    unresolved_markers: list[str]
    uncited_regulatory_sentences: list[str]
    complete: bool
