import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from modelforge_core.schemas import FeaturePolicy, Methodology, ModelingPlan, Thresholds
from modelforge_gateway.providers.fake import ScriptedChatModel
from modelforge_gateway.schemas import GatewayError
from modelforge_orchestrator import grounding
from modelforge_orchestrator.agent_schemas import (
    ChallengeQueryPlan,
    FindingResponseDraft,
    ReReviewDraft,
    ReviewDraft,
)
from modelforge_orchestrator.briefs import parse_brief, plan_from_brief
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[3]
SYNTHETIC = REPO / "examples" / "briefs" / "synthetic.yaml"
SECTION_IDS = [
    "executive_summary",
    "purpose_and_use",
    "regulatory_context",
    "data",
    "methodology",
    "development",
    "performance",
    "limitations",
    "monitoring",
    "governance",
    "appendices",
]
ALLOWED_ALIASES = {
    "executive_summary": {"plan", "profile", "selection", "discrimination", "validation"},
    "purpose_and_use": {"plan"},
    "regulatory_context": {"plan"},
    "data": {"profile", "leakage", "psi", "split", "dictionary"},
    "methodology": {"plan", "binning"},
    "development": {"binning", "selection", "champion", "challenger_model", "calibration"},
    "performance": {
        "validation",
        "discrimination",
        "calibration_tests",
        "stability",
        "sensitivity",
        "benchmark",
    },
    "limitations": {"plan", "validation"},
    "monitoring": {"plan", "validation"},
    "governance": {"plan"},
    "appendices": {"binning", "champion", "validation"},
}
ALLOWED_PATHS = {
    "plan.model_name",
    "plan.portfolio",
    "plan.intended_use",
    "plan.target.horizon",
    "plan.regulatory_scope",
    "profile.n_rows",
    "profile.target_rate",
    "profile.n_features",
    "leakage.duplicate_rows",
    "leakage.flags.count",
    "split.row_ids.train.count",
    "split.row_ids.val.count",
    "split.row_ids.oot.count",
    "split.oot_available",
    "selection.selected.count",
    "selection.dropped.count",
    "champion.features.count",
    "champion.fit_method",
    "champion.intercept",
    "challenger_model.num_trees",
    "calibration.method",
    "discrimination.by_split.val.auc",
    "discrimination.by_split.oot.auc",
    "discrimination.by_split.oot.ks",
    "calibration_tests.hl_p_value",
    "stability.score_psi",
    "benchmark.champion_within_gap",
    "validation.overall",
    "validation.tests[disc.auc.oot].status",
    "validation.tests[cal.binomial].status",
}
REGULATION = re.compile(r"SR 11-7|SR 26-2|SR 21-8|OCC|CECL|IFRS 9|Basel")
PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
UNGROUNDED = "The portfolio default rate was 0.1234 in the development window."


class ScopeProposal(BaseModel):
    plan: ModelingPlan
    rationale: str
    assumptions: list[str]
    open_questions: list[str]


class QueryPlan(BaseModel):
    queries: list[str]


class SectionDraft(BaseModel):
    body_md: str
    citations: list[str] = []


class Unscripted(BaseModel):
    x: int


def _prompt(blocks: dict[str, tuple[str, str]]) -> list[BaseMessage]:
    body = "\n".join(f"```{lang} {name}\n{text}\n```" for name, (lang, text) in blocks.items())
    return [SystemMessage("sys"), HumanMessage(f"Intro text.\n\n{body}\n\nReturn the object.")]


def _plan_dict() -> dict[str, Any]:
    return plan_from_brief(parse_brief(SYNTHETIC.read_bytes())).model_dump(mode="json")


def _section(section_id: str) -> tuple[str, str]:
    return ("json", json.dumps({"id": section_id, "title": section_id, "guidance": "g"}))


def _draft_prompt(
    section_id: str, excerpts: dict[str, Any], passages: list[dict[str, Any]] | None = None
) -> list[BaseMessage]:
    return _prompt(
        {
            "section": _section(section_id),
            "excerpts": ("json", json.dumps(excerpts)),
            "passages": ("json", json.dumps(passages or [])),
        }
    )


def _full_excerpts() -> dict[str, Any]:
    return {
        "plan": _plan_dict(),
        "profile": {"n_rows": 43405, "target_rate": 0.0482, "n_features": 64},
        "leakage": {"duplicate_rows": 0, "flags": []},
        "split": {
            "oot_available": True,
            "row_ids": {"train": ["a", "b"], "val": ["c"], "oot": ["d"]},
        },
        "selection": {"selected": ["f_1", "f_2"], "dropped": [{"feature": "f_3"}]},
        "champion": {"features": [{"feature": "f_1"}], "fit_method": "single", "intercept": -2.95},
        "challenger_model": {"num_trees": 200},
        "calibration": {"method": "platt"},
        "discrimination": {
            "by_split": [
                {"split": "oot", "auc": 0.874, "ks": 0.6},
                {"split": "val", "auc": 0.9, "ks": 0.6},
            ]
        },
        "calibration_tests": {"hl_p_value": 0.3125},
        "stability": {"score_psi": 0.04},
        "benchmark": {"champion_within_gap": True},
        "validation": {
            "overall": "pass",
            "tests": [
                {"test_id": "disc.auc.oot", "status": "pass"},
                {"test_id": "cal.binomial", "status": "warn"},
            ],
        },
    }


PASSAGES = [
    {"citation_id": "k_one", "doc_id": "d1", "section": "s", "text": "t"},
    {"citation_id": "k_two", "doc_id": "d2", "section": "s", "text": "t"},
]


def _templates() -> dict[str, str]:
    text = (files("modelforge_gateway.providers") / "fake_sections.yaml").read_text("utf-8")
    return yaml.safe_load(text)


def _draft(model: ScriptedChatModel, prompt: list[BaseMessage]) -> dict[str, Any]:
    out = model.with_structured_output(SectionDraft, include_raw=True).invoke(prompt)
    assert isinstance(out, dict)
    return out


def test_scope_script_copies_brief_and_cites_first_passage() -> None:
    defaults = {
        "thresholds": Thresholds().model_dump(),
        "feature_policy": FeaturePolicy().model_dump(),
        "methodology": Methodology().model_dump(),
        "sections": SECTION_IDS,
    }
    prompt = _prompt(
        {
            "brief": ("yaml", SYNTHETIC.read_text("utf-8")),
            "defaults": ("json", json.dumps(defaults)),
            "passages": (
                "json",
                json.dumps([{"citation_id": "k_abc", "doc_id": "d", "text": "t"}]),
            ),
        }
    )

    out = ScriptedChatModel().with_structured_output(ScopeProposal, include_raw=True).invoke(prompt)

    assert isinstance(out, dict)
    assert out["parsing_error"] is None
    parsed = out["parsed"]
    assert isinstance(parsed, ScopeProposal)
    assert parsed.plan.model_name == "synthetic_pd"
    assert parsed.plan == plan_from_brief(parse_brief(SYNTHETIC.read_bytes()))
    assert "[K:k_abc]" in parsed.rationale and parsed.rationale.endswith(".")
    assert parsed.assumptions == [
        "The bankruptcy label stands in for the default event.",
        "The dataset's horizon files stand in for origination vintages.",
    ]
    assert parsed.open_questions == []
    raw = out["raw"]
    assert isinstance(raw, AIMessage)
    assert raw.usage_metadata is not None
    usage = raw.usage_metadata
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
    assert raw.response_metadata == {"stop_reason": "end_turn", "model": "scripted-v1"}
    assert ScopeProposal.model_validate_json(str(raw.content)) == parsed


def test_scope_script_without_passages_has_no_marker_and_applies_overrides() -> None:
    brief = yaml.safe_load(SYNTHETIC.read_text("utf-8"))
    brief["thresholds"] = {"auc_min": 0.75}
    del brief["feature_policy"]
    defaults = {
        "thresholds": Thresholds().model_dump(),
        "feature_policy": FeaturePolicy(max_features=7).model_dump(),
        "methodology": Methodology().model_dump(),
        "sections": [],
    }
    prompt = _prompt(
        {
            "brief": ("yaml", yaml.safe_dump(brief)),
            "defaults": ("json", json.dumps(defaults)),
            "passages": ("json", "[]"),
        }
    )

    parsed = ScriptedChatModel().with_structured_output(ScopeProposal).invoke(prompt)

    assert isinstance(parsed, ScopeProposal)
    assert "[K:" not in parsed.rationale
    assert parsed.plan.thresholds.auc_min == 0.75 and parsed.plan.thresholds.ks_min == 0.25
    assert parsed.plan.feature_policy.max_features == 7


def test_query_plan_script_by_section() -> None:
    model = ScriptedChatModel()

    def queries(section_id: str) -> list[str]:
        out = model.with_structured_output(QueryPlan).invoke(
            _prompt({"section": _section(section_id)})
        )
        assert isinstance(out, QueryPlan)
        return out.queries

    assert queries("regulatory_context") == [
        "supervisory expectations for model risk management",
        "scope of SR 26-2 for generative and agentic AI",
    ]
    assert queries("methodology") == [
        "conceptual soundness of a weight of evidence logistic scorecard"
    ]
    assert queries("limitations") == ["bankruptcy as a proxy for default"]
    assert queries("monitoring") == ["ongoing monitoring of a probability of default model"]
    assert queries("governance") == ["model documentation and effective challenge"]
    assert queries("executive_summary") == [
        "model risk management expectations for model documentation"
    ]
    assert queries("data") == []


def test_section_script_emits_grounded_markers_and_is_deterministic() -> None:
    model = ScriptedChatModel()
    excerpts = {
        "profile": {"n_rows": 43405, "target_rate": 0.0482},
        "selection": {"selected": ["f_1", "f_2"], "dropped": []},
        "discrimination": {
            "by_split": [
                {"split": "oot", "auc": 0.874, "ks": 0.6},
                {"split": "val", "auc": 0.9, "ks": 0.6},
            ]
        },
        "validation": {"overall": "pass", "tests": []},
        "plan": _plan_dict(),
    }

    out = _draft(model, _draft_prompt("executive_summary", excerpts))

    assert out["parsing_error"] is None
    draft = out["parsed"]
    assert isinstance(draft, SectionDraft)
    body = draft.body_md
    assert "43405 [A:profile#n_rows]" in body
    assert "0.0482 [A:profile#target_rate]" in body
    assert "2 [A:selection#selected.count]" in body
    assert "0.874 [A:discrimination#by_split.oot.auc]" in body
    assert "pass [A:validation#overall]" in body
    assert "synthetic_pd [A:plan#model_name]" in body
    assert "{{" not in body
    assert draft.citations[:2] == ["[A:plan#model_name]", "[A:plan#portfolio]"]
    assert all(marker in body for marker in draft.citations)
    assert body.count("[A:") == len(draft.citations)
    assert body == _draft(model, _draft_prompt("executive_summary", excerpts))["parsed"].body_md


def test_section_script_drops_sentences_whose_data_is_missing() -> None:
    excerpts = _full_excerpts()
    del excerpts["discrimination"]

    body = _draft(ScriptedChatModel(), _draft_prompt("executive_summary", excerpts))[
        "parsed"
    ].body_md

    assert "[A:discrimination" not in body
    assert "[A:profile#n_rows]" in body
    for section_id in SECTION_IDS:
        empty = _draft(ScriptedChatModel(), _draft_prompt(section_id, {}))["parsed"]
        assert empty.body_md.strip() and "{{" not in empty.body_md, section_id
        assert "[A:" not in empty.body_md and "[K:" not in empty.body_md
        assert empty.citations == []


def test_every_template_renders_fully_with_all_artifacts_and_passages() -> None:
    model = ScriptedChatModel()
    for section_id in SECTION_IDS:
        templates = _templates()
        expected = len(PLACEHOLDER.findall(templates[section_id]))
        draft = _draft(model, _draft_prompt(section_id, _full_excerpts(), PASSAGES))["parsed"]
        assert "{{" not in draft.body_md, section_id
        assert len(draft.citations) == expected, section_id
    regulatory = _draft(model, _draft_prompt("regulatory_context", _full_excerpts(), PASSAGES))[
        "parsed"
    ]
    assert "SR 11-7, SR 26-2 [A:plan#regulatory_scope]" in regulatory.body_md
    assert "[K:k_one]" in regulatory.citations and "[K:k_two]" in regulatory.citations
    performance = _draft(model, _draft_prompt("performance", _full_excerpts(), PASSAGES))[
        "parsed"
    ].body_md
    assert "yes [A:benchmark#champion_within_gap]" in performance
    assert "warn [A:validation#tests[cal.binomial].status]" in performance
    assert "0.3125 [A:calibration_tests#hl_p_value]" in performance


def test_regulatory_sentences_drop_without_passages() -> None:
    body = _draft(ScriptedChatModel(), _draft_prompt("regulatory_context", _full_excerpts()))[
        "parsed"
    ].body_md

    assert body.strip()
    assert not REGULATION.search(body)


def test_null_and_non_finite_values_render_as_na() -> None:
    excerpts = _full_excerpts()
    excerpts["stability"] = {"score_psi": None}
    excerpts["calibration_tests"] = {"hl_p_value": "NaN"}

    body = _draft(ScriptedChatModel(), _draft_prompt("performance", excerpts))["parsed"].body_md

    assert "n/a [A:stability#score_psi]" in body
    assert "n/a [A:calibration_tests#hl_p_value]" in body


def test_templates_follow_the_section_rules() -> None:
    templates = _templates()

    assert sorted(templates) == sorted(SECTION_IDS)
    for section_id, text in templates.items():
        for placeholder in PLACEHOLDER.findall(text):
            if placeholder.startswith("K:"):
                assert section_id == "regulatory_context" and placeholder[2:].isdigit()
                continue
            alias = placeholder.split(".", 1)[0]
            assert alias in ALLOWED_ALIASES[section_id], (section_id, placeholder)
            assert placeholder in ALLOWED_PATHS, (section_id, placeholder)
        if section_id != "regulatory_context":
            assert not REGULATION.search(text), section_id
    for sentence in SENTENCE.split(templates["regulatory_context"].strip()):
        if REGULATION.search(sentence) or "plan.regulatory_scope" in sentence:
            assert re.search(r"\{\{K:\d+\}\}\.$", sentence), sentence
    assert not re.search(r"\d", templates["governance"])
    assert "extends SR 11-7" not in templates["regulatory_context"]


def test_faults_ungrounded_and_refuse() -> None:
    excerpts = _full_excerpts()
    ungrounded = _draft(
        ScriptedChatModel(faults={"data": "ungrounded"}), _draft_prompt("data", excerpts)
    )
    body = ungrounded["parsed"].body_md
    assert body.endswith(UNGROUNDED)
    other = _draft(
        ScriptedChatModel(faults={"data": "ungrounded"}), _draft_prompt("methodology", excerpts)
    )
    assert "0.1234" not in other["parsed"].body_md

    refused = _draft(ScriptedChatModel(faults={"data": "refuse"}), _draft_prompt("data", excerpts))
    assert refused["parsed"] is None and refused["parsing_error"] is None
    raw = refused["raw"]
    assert isinstance(raw, AIMessage)
    assert raw.response_metadata["stop_reason"] == "refusal" and raw.content == ""


def test_unknown_schema_is_config_error() -> None:
    with pytest.raises(GatewayError) as exc:
        ScriptedChatModel().with_structured_output(Unscripted).invoke(
            _prompt({"section": _section("data")})
        )
    assert exc.value.kind == "config"
    assert exc.value.message == "the fake provider has no script for Unscripted"


def test_unstructured_invoke_returns_scripted_json() -> None:
    message = ScriptedChatModel().invoke(_draft_prompt("data", _full_excerpts()))

    assert isinstance(message, AIMessage)
    assert "[A:profile#n_rows]" in json.loads(str(message.content))["body_md"]


def _document(limitations: str, performance: str) -> str:
    return "\n\n".join(
        [
            "# Development document",
            "## 7. Performance and validation results",
            performance,
            "## 8. Limitations and weaknesses",
            limitations,
            "## 9. Ongoing monitoring plan",
            "Score drift is monitored quarterly.",
            "## 10. Governance and lineage",
            "Each approval gate is recorded.",
        ]
    )


def _review_prompt(document: str, event: str) -> list[BaseMessage]:
    plan = _plan_dict()
    plan["target"]["event"] = event
    validation = {
        "overall": "warn",
        "tests": [
            {
                "test_id": "cal.hl",
                "name": "Hosmer-Lemeshow test",
                "status": "warn",
                "value": 0.03,
                "threshold": 0.05,
                "message": "m",
            }
        ],
    }
    return _prompt(
        {
            "document": ("markdown", document),
            "validation": ("json", json.dumps(validation)),
            "plan": ("json", json.dumps(plan)),
            "passages": ("json", json.dumps(PASSAGES)),
            "prechecks": ("json", "[]"),
        }
    )


def _review(prompt: list[BaseMessage]) -> ReviewDraft:
    out = ScriptedChatModel().with_structured_output(ReviewDraft, include_raw=True).invoke(prompt)
    assert isinstance(out, dict)
    assert out["parsing_error"] is None
    parsed = out["parsed"]
    assert isinstance(parsed, ReviewDraft)
    return parsed


MONITORING_FINDING = {
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
PROXY_FINDING = {
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
GOVERNANCE_FINDING = {
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


def test_fake_scripts_challenge_query_plan() -> None:
    prompt = _prompt(
        {
            "sections": ("json", json.dumps(["Executive summary", "Data"])),
            "scope": ("json", json.dumps(["SR 11-7", "SR 26-2"])),
            "statuses": ("json", json.dumps({"disc.auc.oot": "pass", "cal.hl": "warn"})),
            "prechecks": ("json", "[]"),
        }
    )

    out = ScriptedChatModel().with_structured_output(ChallengeQueryPlan).invoke(prompt)

    assert isinstance(out, ChallengeQueryPlan)
    assert out.queries == [
        "effective challenge and independent validation",
        "ongoing monitoring expectations",
    ]


def test_fake_review_raises_the_scripted_findings() -> None:
    performance = (
        "The AUC is 0.874 [A:validation#tests[disc.auc.oot].value]. "
        "A bootstrap test was performed and passed. "
        "A stress test was performed [A:validation#overall]."
    )
    document = _document("The sample covers five vintages.", performance)

    draft = _review(_review_prompt(document, "bankruptcy, as a proxy for default"))

    findings = [finding.model_dump() for finding in draft.findings]
    assert len(findings) == 4
    assert findings[0] == MONITORING_FINDING
    assert findings[1] == PROXY_FINDING
    assert findings[2] == {
        "severity": "high",
        "category": "performance",
        "title": "Claimed test without artifact evidence",
        "description": (
            "The performance section claims a test without citing the artifact that "
            "records it: A bootstrap test was performed and passed."
        ),
        "evidence": ["performance"],
        "required_action": (
            "Cite the validation result that records the test, or remove the claim."
        ),
    }
    assert findings[3] == GOVERNANCE_FINDING


def test_fake_review_omits_proxy_finding_when_limitations_mention_it() -> None:
    document = _document(
        "Bankruptcy is used as a Proxy for default.", "Discrimination is reported below."
    )

    proxied = _review(_review_prompt(document, "bankruptcy, as a proxy for default"))
    unproxied = _review(_review_prompt(_document("None.", "None."), "default within a year"))

    assert [f.model_dump() for f in proxied.findings] == [MONITORING_FINDING, GOVERNANCE_FINDING]
    assert [f.model_dump() for f in unproxied.findings] == [
        MONITORING_FINDING,
        GOVERNANCE_FINDING,
    ]


def _finding(finding_id: str, status: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": "medium",
        "category": "monitoring",
        "title": "t",
        "description": "d",
        "evidence": evidence,
        "required_action": "a",
        "status": status,
        "response": "r",
        "response_evidence": [],
    }


def test_fake_re_review_verdicts_by_status_and_changed_sections() -> None:
    findings = [
        _finding("F-1", "accepted", ["monitoring", "[A:plan#thresholds.psi_warn]"]),
        _finding("F-2", "accepted", ["limitations"]),
        _finding("F-3", "disputed", ["governance"]),
    ]
    prompt = _prompt(
        {
            "findings": ("json", json.dumps(findings)),
            "changed_sections": ("json", json.dumps({"monitoring": "New body.", "data": "x"})),
            "validation": ("json", json.dumps({"overall": "pass", "tests": []})),
            "plan": ("json", json.dumps(_plan_dict())),
        }
    )

    out = ScriptedChatModel().with_structured_output(ReReviewDraft).invoke(prompt)

    assert isinstance(out, ReReviewDraft)
    assert [v.model_dump() for v in out.verdicts] == [
        {
            "finding_id": "F-1",
            "status": "closed",
            "note": "the regenerated section addresses the finding",
        },
        {"finding_id": "F-2", "status": "open", "note": "accepted but not remediated"},
        {
            "finding_id": "F-3",
            "status": "disputed",
            "note": "dispute noted; remains visible at sign-off",
        },
    ]


def test_unstructured_invoke_infers_the_challenger_schemas() -> None:
    model = ScriptedChatModel()
    plan_queries = _prompt({"statuses": ("json", "{}"), "prechecks": ("json", "[]")})
    re_review = _prompt({"findings": ("json", "[]"), "changed_sections": ("json", "{}")})

    review = model.invoke(_review_prompt(_document("None.", "None."), "default"))
    queries = model.invoke(plan_queries)
    verdicts = model.invoke(re_review)

    assert ReviewDraft.model_validate_json(str(review.content)).findings[0].category == (
        "monitoring"
    )
    assert ChallengeQueryPlan.model_validate_json(str(queries.content)).queries
    assert ReReviewDraft.model_validate_json(str(verdicts.content)).verdicts == []


def _open_finding(
    finding_id: str, category: str, title: str, evidence: list[str], source: str = "review"
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "round": 1,
        "severity": "medium",
        "category": category,
        "title": title,
        "description": "d",
        "evidence": evidence,
        "required_action": "a",
        "status": "open",
        "source": source,
        "check_key": None,
    }


def _respond(finding: dict[str, Any]) -> FindingResponseDraft:
    sections = {"monitoring": "Score drift is monitored.", "limitations": "None."}
    prompt = _prompt(
        {
            "finding": ("json", json.dumps(finding)),
            "sections": ("json", json.dumps(sections)),
            "excerpts": ("json", json.dumps({"plan": _plan_dict()})),
        }
    )
    out = ScriptedChatModel().with_structured_output(FindingResponseDraft).invoke(prompt)
    assert isinstance(out, FindingResponseDraft)
    return out


def test_fake_responds_by_category() -> None:
    governance = _respond(
        _open_finding("F-4", "governance", "Approver identities are not named", ["governance"])
    )
    proxy = _respond(
        _open_finding(
            "F-2",
            "data",
            "Dataset proxies are not disclosed as limitations",
            ["limitations", "[A:plan#target.event]"],
        )
    )
    monitoring = _respond(
        _open_finding(
            "F-1", "monitoring", "Monitoring thresholds are not quantified", ["monitoring"]
        )
    )

    assert governance.model_dump() == {
        "decision": "dispute",
        "action": (
            "Approvals are recorded in the lineage approvals table and the sealed manifest, "
            "not in the document body."
        ),
        "evidence": ["[A:plan#model_name]"],
        "sections_to_regenerate": [],
    }
    assert proxy.model_dump() == {
        "decision": "dispute",
        "action": (
            "The brief defines the target as a proxy for default and the limitations section "
            "states it."
        ),
        "evidence": ["[A:plan#target.event]"],
        "sections_to_regenerate": [],
    }
    assert monitoring.model_dump() == {
        "decision": "accept",
        "action": "Revised the monitoring section(s) to address the finding.",
        "evidence": [],
        "sections_to_regenerate": ["monitoring"],
    }


def _rewrite_prompt(findings: list[dict[str, Any]]) -> list[BaseMessage]:
    excerpts = _full_excerpts()
    excerpts["validation"]["tests"].append({"test_id": "cal.hl", "status": "warn"})
    return _prompt(
        {
            "section": _section("performance"),
            "excerpts": ("json", json.dumps(excerpts)),
            "passages": ("json", json.dumps(PASSAGES)),
            "findings": ("json", json.dumps(findings)),
        }
    )


def test_fake_rewrite_renders_cited_test_status_and_quotes_titles() -> None:
    marker = "[A:validation#tests[cal.hl].status]"
    findings = [
        _open_finding("F-1", "performance", "Calibration warning", [marker], source="precheck"),
        _open_finding("F-2", "performance", "AUC 0.70 breach", ["performance"]),
    ]
    model = ScriptedChatModel()
    plain = _draft(model, _draft_prompt("performance", _full_excerpts(), PASSAGES))["parsed"]

    draft = _draft(model, _rewrite_prompt(findings))["parsed"]

    status = f"The validation test `cal.hl` has outcome warn {marker}."
    title = "This revision addresses the reviewer's finding `AUC 0.70 breach`."
    assert draft.body_md == f"{plain.body_md} {status} {title}"
    assert draft.citations == [*plain.citations, marker]


def test_fake_rewrite_quotes_finding_titles_in_code_spans() -> None:
    finding = _open_finding("F-3", "performance", "AUC `0.70` is below 0.75", ["performance"])

    body = _draft(ScriptedChatModel(), _rewrite_prompt([finding]))["parsed"].body_md

    last_sentence = SENTENCE.split(body)[-1]
    assert last_sentence == (
        "This revision addresses the reviewer's finding `AUC '0.70' is below 0.75`."
    )
    assert grounding.numbers_in(grounding.strip_non_claims(last_sentence)) == []


def test_unstructured_invoke_infers_the_finding_response() -> None:
    finding = _open_finding("F-1", "monitoring", "t", ["monitoring"])
    prompt = _prompt(
        {
            "finding": ("json", json.dumps(finding)),
            "sections": ("json", json.dumps({"monitoring": "Body."})),
            "excerpts": ("json", "{}"),
        }
    )

    message = ScriptedChatModel().invoke(prompt)

    response = FindingResponseDraft.model_validate_json(str(message.content))
    assert response.sections_to_regenerate == ["monitoring"]
