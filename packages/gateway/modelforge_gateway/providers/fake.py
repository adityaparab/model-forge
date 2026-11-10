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
HEADING = re.compile(r"^## \d+\. (.+)$", re.M)
TEST_STATUS = re.compile(r"\[A:validation#tests\[(?P<id>[^\]]+)\]\.status\]")

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
CHALLENGE_QUERIES = [
    "effective challenge and independent validation",
    "ongoing monitoring expectations",
]
MONITORING_FINDING: dict[str, Any] = {
    "severity": "medium",
    "category": "monitoring",
    "title": "Monitoring thresholds are not quantified",
    "description": (
        "The monitoring section names the metrics but states no numeric thresholds or "
        "escalation triggers."
    ),
    "evidence": ["monitoring", "[A:plan#thresholds.psi_warn]"],
    "required_action": (
        "State the numeric thresholds and the escalation path for each monitored metric."
    ),
}
PROXY_FINDING: dict[str, Any] = {
    "severity": "medium",
    "category": "data",
    "title": "Dataset proxies are not disclosed as limitations",
    "description": (
        "The target event is a proxy for default, but the limitations section does not "
        "disclose the proxy or its effect on the estimates."
    ),
    "evidence": ["limitations", "[A:plan#target.event]"],
    "required_action": (
        "Disclose each dataset proxy in the limitations section with its effect on the use "
        "of the model."
    ),
}
GOVERNANCE_FINDING: dict[str, Any] = {
    "severity": "low",
    "category": "governance",
    "title": "Approver identities are not named in the governance section",
    "description": (
        "The governance section describes the approval gates but does not name the role "
        "or identity that approves at each gate."
    ),
    "evidence": ["governance"],
    "required_action": "Name the approver role or identity for each approval gate.",
}
LIMITATIONS_TITLE = "Limitations and weaknesses"
PERFORMANCE_TITLE = "Performance and validation results"
VERDICT_CLOSED = "the regenerated section addresses the finding"
VERDICT_OPEN = "accepted but not remediated"
VERDICT_DISPUTED = "dispute noted; remains visible at sign-off"
GOVERNANCE_DISPUTE = (
    "Approvals are recorded in the lineage approvals table and the sealed manifest, not in the "
    "document body."
)
PROXY_DISPUTE = (
    "The brief defines the target as a proxy for default and the limitations section states it."
)
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


def _code_span(text: str) -> str:
    """``text`` as one inline code span, as the document node writes one: a backtick inside
    it becomes ``'`` and whitespace runs collapse."""
    return "`" + " ".join(text.replace("`", "'").split()) + "`"


def _status_sentence(evidence: list[str], excerpts: dict[str, Any]) -> tuple[str, str] | None:
    """The sentence stating the first cited test status the excerpts resolve, and its marker."""
    if "validation" not in excerpts:
        return None
    for item in evidence:
        match = TEST_STATUS.fullmatch(item)
        if match is None:
            continue
        test_id, marker = match.group("id"), match.group(0)
        try:
            value = resolve_path(excerpts["validation"], f"tests[{test_id}].status")
            rendered = _render_value(value, marker)
        except (PathError, _UnavailableError):
            continue
        return f"The validation test {_code_span(test_id)} has outcome {rendered}.", marker
    return None


def _finding_sentences(
    findings: list[dict[str, Any]], excerpts: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """One sentence per finding a rewrite addresses, and the markers they emit."""
    sentences: list[str] = []
    citations: list[str] = []
    for finding in findings:
        status = _status_sentence(finding.get("evidence", []), excerpts)
        if status is None:
            title = _code_span(str(finding.get("title", "")))
            sentences.append(f"This revision addresses the reviewer's finding {title}.")
        else:
            sentences.append(status[0])
            citations.append(status[1])
    return sentences, citations


def _section_draft(blocks: dict[str, str], fault: Fault | None) -> dict[str, Any]:
    section_id = _section_id(blocks) or ""
    template = section_templates().get(section_id, "")
    excerpts = json.loads(blocks.get("excerpts", "{}"))
    body, citations = render_section(template, excerpts, _passages(blocks))
    if "findings" in blocks:
        sentences, markers = _finding_sentences(json.loads(blocks["findings"]), excerpts)
        body = " ".join([body, *sentences]).strip()
        citations.extend(markers)
    if fault == "ungrounded":
        body = f"{body} {UNGROUNDED_SENTENCE}".strip()
    return {"body_md": body, "citations": citations}


def _document_sections(markdown: str) -> dict[str, str]:
    """Section bodies of a rendered document keyed by their ``## <n>. <title>`` titles."""
    parts = HEADING.split(markdown)
    # Splitting on the capturing heading puts the titles at the odd indices.
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts), 2)}


def _unevidenced_claim(sentence: str) -> dict[str, Any]:
    return {
        "severity": "high",
        "category": "performance",
        "title": "Claimed test without artifact evidence",
        "description": (
            "The performance section claims a test without citing the artifact that records "
            f"it: {sentence}"
        ),
        "evidence": ["performance"],
        "required_action": "Cite the validation result that records the test, or remove the claim.",
    }


def _review_draft(blocks: dict[str, str]) -> dict[str, Any]:
    sections = _document_sections(blocks.get("document", ""))
    plan = json.loads(blocks.get("plan", "{}"))
    event = str(plan.get("target", {}).get("event", ""))
    findings: list[dict[str, Any]] = [dict(MONITORING_FINDING)]
    if "proxy" in event.lower() and "proxy" not in sections.get(LIMITATIONS_TITLE, "").lower():
        findings.append(dict(PROXY_FINDING))
    for sentence in SENTENCE_BREAK.split(sections.get(PERFORMANCE_TITLE, "")):
        if "was performed" in sentence and "[A:" not in sentence:
            findings.append(_unevidenced_claim(sentence))
    findings.append(dict(GOVERNANCE_FINDING))
    return {"findings": findings}


def _verdict(finding: dict[str, Any], changed: dict[str, Any]) -> dict[str, str]:
    if finding["status"] == "disputed":
        status, note = "disputed", VERDICT_DISPUTED
    elif finding["status"] == "accepted" and any(item in changed for item in finding["evidence"]):
        status, note = "closed", VERDICT_CLOSED
    else:
        status, note = "open", VERDICT_OPEN
    return {"finding_id": finding["id"], "status": status, "note": note}


def _re_review_draft(blocks: dict[str, str]) -> dict[str, Any]:
    findings = json.loads(blocks.get("findings", "[]"))
    changed = json.loads(blocks.get("changed_sections", "{}"))
    return {"verdicts": [_verdict(finding, changed) for finding in findings]}


def _finding_response(blocks: dict[str, str]) -> dict[str, Any]:
    finding = json.loads(blocks["finding"])
    if finding["category"] == "governance":
        return {
            "decision": "dispute",
            "action": GOVERNANCE_DISPUTE,
            "evidence": ["[A:plan#model_name]"],
            "sections_to_regenerate": [],
        }
    if finding["category"] == "data" and "prox" in finding["title"].lower():
        return {
            "decision": "dispute",
            "action": PROXY_DISPUTE,
            "evidence": ["[A:plan#target.event]"],
            "sections_to_regenerate": [],
        }
    sections = json.loads(blocks.get("sections", "{}"))
    regenerate = [item for item in finding["evidence"] if item in sections]
    return {
        "decision": "accept",
        "action": f"Revised the {', '.join(regenerate)} section(s) to address the finding.",
        "evidence": [],
        "sections_to_regenerate": regenerate,
    }


def script(name: str, blocks: dict[str, str], fault: Fault | None) -> dict[str, Any]:
    if name == "ScopeProposal":
        return _scope_proposal(blocks)
    if name == "QueryPlan":
        return _query_plan(blocks)
    if name == "SectionDraft":
        return _section_draft(blocks, fault)
    if name == "ChallengeQueryPlan":
        return {"queries": list(CHALLENGE_QUERIES)}
    if name == "ReviewDraft":
        return _review_draft(blocks)
    if name == "ReReviewDraft":
        return _re_review_draft(blocks)
    if name == "FindingResponseDraft":
        return _finding_response(blocks)
    raise GatewayError("config", f"the fake provider has no script for {name}")


def _infer_schema_name(blocks: dict[str, str]) -> str:
    if "brief" in blocks:
        return "ScopeProposal"
    if "finding" in blocks:
        return "FindingResponseDraft"
    if "changed_sections" in blocks:
        return "ReReviewDraft"
    if "prechecks" in blocks and "document" in blocks:
        return "ReviewDraft"
    if "statuses" in blocks:
        return "ChallengeQueryPlan"
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
