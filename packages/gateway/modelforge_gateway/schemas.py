"""Request and response schemas for gateway calls."""

from typing import Any, Literal

from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import ArtifactRef
from pydantic import BaseModel, ConfigDict

Purpose = Literal["scope", "document", "challenge", "remediate", "judge"]
Effort = Literal["low", "medium", "high"]
Provider = Literal["anthropic", "ollama", "lmstudio", "fake"]
GatewayErrorKind = Literal["schema", "refusal", "cost_cap", "redaction", "provider", "config"]


class Message(StrictModel):
    role: Literal["system", "user"]
    content: str


class CallMetadata(StrictModel):
    run_id: str
    step_id: str
    section_id: str | None = None


class RedactionContext(StrictModel):
    restricted_terms: list[str] = []


class CompletionRequest(StrictModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)  # merged with StrictModel config

    purpose: Purpose
    prompt_name: str
    prompt_version: int
    system: str
    messages: list[Message]
    output_schema: type[BaseModel]
    effort: Effort | None = None
    max_output_tokens: int = 4096
    cache_prefix: bool = True
    redaction: RedactionContext = RedactionContext()
    metadata: CallMetadata


class Usage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class CompletionResult(StrictModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)  # merged with StrictModel config

    text: str | None
    parsed: BaseModel
    provider: str
    model: str
    prompt_name: str
    prompt_version: int
    prompt_hash: str
    request_hash: str
    usage: Usage
    cost_usd: float
    latency_ms: int
    stop_reason: str | None
    schema_valid: bool
    attempts: int
    raw_ref: ArtifactRef


class LlmResponse(StrictModel):
    """Body of the ``llm_response`` raw artifact stored for every attempt."""

    provider: str
    model: str
    prompt_name: str
    prompt_version: int
    prompt_hash: str
    request_hash: str
    attempt: int
    text: str | None
    parsed: dict[str, Any] | None
    usage: Usage
    stop_reason: str | None


class GatewayError(Exception):
    """A gateway failure; ``kind`` selects how the run is parked."""

    def __init__(
        self,
        kind: GatewayErrorKind,
        message: str,
        *,
        purpose: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind: GatewayErrorKind = kind
        self.message = message
        self.purpose = purpose
        self.provider = provider
        self.model = model

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "message": self.message,
            "purpose": self.purpose,
            "provider": self.provider,
            "model": self.model,
        }
