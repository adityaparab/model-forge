"""Value types shared by the MCP servers and their clients."""

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
