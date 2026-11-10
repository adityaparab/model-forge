"""The ``fake`` provider: a deterministic chat model for offline runs and tests.

It reads the fenced blocks of the prompt layout contract (```` ```<lang> <name> ````) and
scripts a valid object for the requested output schema, keyed on the schema's class name.
"""

import json
import re
from functools import cache
from importlib.resources import files
from typing import Any, Literal

import yaml
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from modelforge_lineage.citations import PathError, format_number, resolve_path
from pydantic import BaseModel

from modelforge_gateway.schemas import GatewayError

Fault = Literal["ungrounded", "refuse"]

BLOCK = re.compile(r"```(\w+) (\w+)\n(.*?)```", re.S)
PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
UNGROUNDED_SENTENCE = "The portfolio default rate was 0.1234 in the development window."
NON_FINITE = {"NaN", "Infinity", "-Infinity"}

ASSUMPTIONS = [
    "The bankruptcy label stands in for the default event.",
    "The dataset's horizon files stand in for origination vintages.",
]
QUERIES: dict[str, list[str]] = {
    "regulatory_context": [
        "supervisory expectations for model risk management",
        "scope of SR 26-2 for generative and agentic AI",
    ],
    "methodology": ["conceptual soundness of a weight of evidence logistic scorecard"],
    "limitations": ["bankruptcy as a proxy for default"],
    "monitoring": ["ongoing monitoring of a probability of default model"],
    "governance": ["model documentation and effective challenge"],
    "executive_summary": ["model risk management expectations for model documentation"],
}
PLAN_FIELDS = (
    "model_name",
    "portfolio",
    "target",
    "vintage_policy",
    "intended_use",
    "regulatory_scope",
    "tier",
)


class _UnavailableError(Exception):
    """A placeholder whose value cannot be rendered; its sentence is dropped."""


@cache
def section_templates() -> dict[str, str]:
    text = (files("modelforge_gateway.providers") / "fake_sections.yaml").read_text("utf-8")
    return yaml.safe_load(text)


def parse_blocks(text: str) -> dict[str, str]:
    return {name: body for _lang, name, body in BLOCK.findall(text)}


def _passages(blocks: dict[str, str]) -> list[dict[str, Any]]:
    passages = json.loads(blocks.get("passages", "[]"))
    return passages if isinstance(passages, list) else []


def _section_id(blocks: dict[str, str]) -> str | None:
    if "section" not in blocks:
        return None
    return json.loads(blocks["section"]).get("id")


def _render_value(value: Any, marker: str) -> str:
    if isinstance(value, bool):
        return f"{'yes' if value else 'no'} {marker}"
    if value is None or (isinstance(value, str) and value in NON_FINITE):
        return f"n/a {marker}"
    if isinstance(value, (int, float, str)):
        # format_number gives n/a for non-finite floats.
        text = value if isinstance(value, str) else format_number(value)
        return f"{text} {marker}"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return f"{', '.join(value)} {marker}"
    raise _UnavailableError(marker)


def _render_placeholder(
    placeholder: str, excerpts: dict[str, Any], passages: list[dict[str, Any]]
) -> tuple[str, str]:
    """The rendered text and the marker it emits."""
    if placeholder.startswith("K:"):
        index = int(placeholder[2:])
        if index >= len(passages):
            raise _UnavailableError(placeholder)
        marker = f"[K:{passages[index]['citation_id']}]"
        return marker, marker
    alias, _, path = placeholder.partition(".")
    if alias not in excerpts:
        raise _UnavailableError(placeholder)
    try:
        value = resolve_path(excerpts[alias], path)
    except PathError as exc:
        raise _UnavailableError(placeholder) from exc
    marker = f"[A:{alias}#{path}]"
    return _render_value(value, marker), marker


def render_section(
    template: str, excerpts: dict[str, Any], passages: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    """The body with every placeholder rendered, dropping sentences that cannot render."""
    sentences: list[str] = []
    citations: list[str] = []
    for sentence in SENTENCE_BREAK.split(template.strip()):
        # Splitting on the capturing pattern puts the placeholders at the odd indices.
        parts = PLACEHOLDER.split(sentence)
        markers: list[str] = []
        try:
            for index in range(1, len(parts), 2):
                parts[index], marker = _render_placeholder(parts[index], excerpts, passages)
                markers.append(marker)
        except _UnavailableError:
            continue
        sentences.append("".join(parts))
        citations.extend(markers)
    return " ".join(sentences), citations


def _scope_proposal(blocks: dict[str, str]) -> dict[str, Any]:
    brief = yaml.safe_load(blocks["brief"])
    defaults = json.loads(blocks["defaults"])
    plan = {field: brief[field] for field in PLAN_FIELDS}
    plan["feature_policy"] = brief.get("feature_policy", defaults["feature_policy"])
    plan["methodology"] = defaults["methodology"]
    plan["thresholds"] = {**defaults["thresholds"], **(brief.get("thresholds") or {})}
    rationale = (
        f"The plan follows the brief for {brief['model_name']} and the default standards "
        "for a weight-of-evidence PD scorecard"
    )
    passages = _passages(blocks)
    if passages:
        rationale += f" [K:{passages[0]['citation_id']}]"
    return {
        "plan": plan,
        "rationale": rationale + ".",
        "assumptions": list(ASSUMPTIONS),
        "open_questions": [],
    }


def _query_plan(blocks: dict[str, str]) -> dict[str, Any]:
    return {"queries": list(QUERIES.get(_section_id(blocks) or "", []))}


def _section_draft(blocks: dict[str, str], fault: Fault | None) -> dict[str, Any]:
    section_id = _section_id(blocks) or ""
    template = section_templates().get(section_id, "")
    excerpts = json.loads(blocks.get("excerpts", "{}"))
    body, citations = render_section(template, excerpts, _passages(blocks))
    if fault == "ungrounded":
        body = f"{body} {UNGROUNDED_SENTENCE}".strip()
    return {"body_md": body, "citations": citations}


def script(name: str, blocks: dict[str, str], fault: Fault | None) -> dict[str, Any]:
    if name == "ScopeProposal":
        return _scope_proposal(blocks)
    if name == "QueryPlan":
        return _query_plan(blocks)
    if name == "SectionDraft":
        return _section_draft(blocks, fault)
    raise GatewayError("config", f"the fake provider has no script for {name}")


def _infer_schema_name(blocks: dict[str, str]) -> str:
    if "brief" in blocks:
        return "ScopeProposal"
    if "excerpts" in blocks:
        return "SectionDraft"
    return "QueryPlan"


def _prompt_text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.text) for m in messages if isinstance(m, HumanMessage))


class ScriptedChatModel(BaseChatModel):
    """Deterministic stand-in for a chat model; ``faults`` are keyed by section id."""

    faults: dict[str, Fault] = {}
    model_name: str = "scripted-v1"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _raw(self, prompt_text: str, json_text: str, stop_reason: str) -> AIMessage:
        input_tokens, output_tokens = len(prompt_text) // 4, len(json_text) // 4
        return AIMessage(
            content=json_text,
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            response_metadata={"stop_reason": stop_reason, "model": self.model_name},
        )

    def _fault(self, blocks: dict[str, str]) -> Fault | None:
        section_id = _section_id(blocks)
        return self.faults.get(section_id) if section_id is not None else None

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt_text = _prompt_text(messages)
        blocks = parse_blocks(prompt_text)
        answer = script(_infer_schema_name(blocks), blocks, self._fault(blocks))
        message = self._raw(prompt_text, json.dumps(answer), "end_turn")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise GatewayError("config", "the fake provider needs a Pydantic output schema")
        model_type: type[BaseModel] = schema

        def respond(model_input: LanguageModelInput) -> Any:
            prompt_text = _prompt_text(self._convert_input(model_input).to_messages())
            blocks = parse_blocks(prompt_text)
            fault = self._fault(blocks)
            if fault == "refuse":
                raw = self._raw(prompt_text, "", "refusal")
                return {"raw": raw, "parsed": None, "parsing_error": None} if include_raw else None
            answer = script(model_type.__name__, blocks, fault)
            parsed = model_type.model_validate(answer)
            json_text = parsed.model_dump_json()
            raw = self._raw(prompt_text, json_text, "end_turn")
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(respond)
