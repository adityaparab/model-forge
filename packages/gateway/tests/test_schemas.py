import pytest
from modelforge_gateway.schemas import CallMetadata, CompletionRequest, GatewayError, Message
from pydantic import BaseModel, ValidationError


class _Out(BaseModel):
    answer: str


def test_gateway_error_to_dict_and_str() -> None:
    err = GatewayError("cost_cap", "over", purpose="scope", provider="anthropic", model="m")
    assert err.to_dict() == {
        "kind": "cost_cap",
        "message": "over",
        "purpose": "scope",
        "provider": "anthropic",
        "model": "m",
    }
    assert str(err) == "cost_cap: over"


def _request(**overrides: object) -> CompletionRequest:
    fields: dict[str, object] = {
        "purpose": "scope",
        "prompt_name": "scope/propose_plan",
        "prompt_version": 1,
        "system": "sys",
        "messages": [Message(role="user", content="hi")],
        "output_schema": _Out,
        "metadata": CallMetadata(run_id="r", step_id="s"),
    }
    fields.update(overrides)
    return CompletionRequest.model_validate(fields)


def test_completion_request_defaults_and_schema_type() -> None:
    req = _request()
    assert req.output_schema is _Out
    assert req.effort is None and req.max_output_tokens == 4096 and req.cache_prefix is True
    assert req.redaction.restricted_terms == []


def test_completion_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _request(unexpected=1)
