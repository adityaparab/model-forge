"""The challenger's deterministic pre-checks over hand-built contexts."""

from typing import Any

from modelforge_mcp.schemas import CompletenessReport, SectionSpec, TemplateSections
from modelforge_mcp.servers.doc_renderer import BANNER
from modelforge_orchestrator.prechecks import PrecheckContext, PrecheckFinding, run_prechecks

DISC = "d" * 64
VAL = "e" * 64
SECTION_IDS = (
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
)
TEMPLATE = TemplateSections(
    template_id="sr11-7-dev-doc",
    sections=[
        SectionSpec(
            id=i, title=i.title(), required=i != "appendices", artifact_kinds=[], guidance="g"
        )
        for i in SECTION_IDS
    ],
)
VALIDATION: dict[str, Any] = {
    "tests": [
        {
            "test_id": "disc.auc.oot",
            "name": "Out-of-time AUC",
            "status": "pass",
            "value": 0.874,
            "threshold": ">= 0.70",
            "message": "AUC above threshold",
        },
        {
            "test_id": "cal.hl",
            "name": "Hosmer-Lemeshow test",
            "status": "warn",
            "value": 0.03,
            "threshold": ">= 0.05",
            "message": "p-value below threshold",
        },
        {
            "test_id": "stab.psi.score",
            "name": "Score PSI",
            "status": "fail",
            "value": 0.31,
            "threshold": "< 0.25",
            "message": "score population shifted",
        },
        {
            "test_id": "sens.signs",
            "name": "Coefficient sign check",
            "status": "not_applicable",
            "value": None,
            "threshold": "all signs as expected",
            "message": "No sensitivity results exist",
        },
    ]
}
ARTIFACTS: dict[str, Any] = {
    DISC: {"by_split": [{"split": "oot", "auc": 0.874}]},
    VAL: VALIDATION,
}
PASSAGES = {
    "k_aaa": ("sr11-7", "Model risk management requires effective challenge of models."),
    "k_bbb": ("ifrs9-ecl", "Expected credit losses are measured over twelve months."),
    "k_sr26": ("sr26-2", "This guidance supersedes SR 11-7 for traditional models."),
}
PERFORMANCE = (
    f"The AUC on the out-of-time split is 0.874 [A:{DISC}#by_split.oot.auc]. "
    f"The calibration check returned a warning [A:{VAL}#tests[cal.hl].status]. "
    "The score PSI test stab.psi.score failed. "
    "The coefficient sign check is not applicable because no sensitivity results exist."
)
REGULATORY = "SR 11-7 requires effective challenge [K:k_aaa]."


def _sections(**bodies: str) -> dict[str, str]:
    base = {i: "This section is complete." for i in SECTION_IDS}
    base["performance"] = PERFORMANCE
    base["regulatory_context"] = REGULATORY
    base.update(bodies)
    return base


def _ctx(sections: dict[str, str], missing: list[str] | None = None) -> PrecheckContext:
    return PrecheckContext(
        sections=sections,
        template=TEMPLATE,
        completeness=CompletenessReport(
            template_id="sr11-7-dev-doc",
            document_id="f" * 64,
            missing_sections=missing or [],
            unresolved_markers=[],
            uncited_regulatory_sentences=[],
            complete=not missing,
        ),
        validation=VALIDATION,
        validation_id=VAL,
        artifact_kinds={DISC: "discrimination", VAL: "validation_report"},
        load_artifact=lambda i: ARTIFACTS[i],
        passages=PASSAGES,
    )


def _with_prefix(findings: list[PrecheckFinding], prefix: str) -> list[PrecheckFinding]:
    return [f for f in findings if f.check_key.startswith(prefix)]


def test_a_clean_document_raises_nothing() -> None:
    assert run_prechecks(_ctx(_sections())) == []


def test_missing_and_bannered_sections() -> None:
    sections = _sections(governance=BANNER, appendices=BANNER + "\n\nPartial.")
    del sections["monitoring"]
    findings = run_prechecks(_ctx(sections, missing=["governance", "data"]))
    missing = _with_prefix(findings, "missing_section:")
    assert [f.check_key for f in missing] == [
        "missing_section:data",
        "missing_section:monitoring",
        "missing_section:governance",
    ]
    assert all(f.severity == "critical" and f.category == "documentation" for f in missing)
    assert [f.evidence for f in missing] == [["data"], ["monitoring"], ["governance"]]
    bannered = _with_prefix(findings, "bannered_section:")
    assert [(f.check_key, f.severity, f.category, f.evidence) for f in bannered] == [
        ("bannered_section:appendices", "high", "documentation", ["appendices"]),
    ]
    clean = run_prechecks(_ctx(_sections()))
    assert _with_prefix(clean, "missing_section:") == _with_prefix(clean, "bannered_section:") == []


def test_quoted_failures_under_the_banner_are_not_claims() -> None:
    quoted = (
        f"{BANNER}\n\n- foreign_passages: `[K:k_zzz]`\n"
        "- uncited_regulatory_sentences: `Basel III applies [K:k_bbb] at 0.875.`\n\n"
        f"{REGULATORY}"
    )
    findings = run_prechecks(_ctx(_sections(regulatory_context=quoted)))
    assert [f.check_key for f in findings] == ["bannered_section:regulatory_context"]
    unquoted = quoted.replace("`", "")
    keys = [f.check_key for f in run_prechecks(_ctx(_sections(regulatory_context=unquoted)))]
    assert "reverify:regulatory_context" in keys
    assert any(k.startswith("miscited_regulation:regulatory_context:") for k in keys)


def test_undiscussed_tests_by_marker_name_or_id() -> None:
    by_marker = _sections(
        performance=f"Calibration warned [A:{VAL}#tests[cal.hl].status]. Score PSI is "
        "stab.psi.score and failed. Coefficient sign check: not applicable."
    )
    assert _with_prefix(run_prechecks(_ctx(by_marker)), "undiscussed_test:") == []
    by_name = _sections(limitations="The hosmer-lemeshow TEST warned. STAB.PSI.SCORE failed.")
    by_name["performance"] = "The coefficient sign check is not available."
    assert _with_prefix(run_prechecks(_ctx(by_name)), "undiscussed_test:") == []
    silent = _sections(performance="Coefficient sign check is not applicable.")
    findings = _with_prefix(run_prechecks(_ctx(silent)), "undiscussed_test:")
    assert [(f.check_key, f.severity, f.category) for f in findings] == [
        ("undiscussed_test:cal.hl", "medium", "performance"),
        ("undiscussed_test:stab.psi.score", "high", "performance"),
    ]
    assert findings[0].evidence == ["performance", "[A:validation#tests[cal.hl].status]"]
    assert findings[1].evidence == ["performance", "[A:validation#tests[stab.psi.score].status]"]


def test_unaddressed_not_applicable() -> None:
    for text in (
        "Test sens.signs is NOT_APPLICABLE.",
        "The Coefficient Sign Check is not available for this model.",
    ):
        findings = run_prechecks(_ctx(_sections(limitations=text)))
        assert _with_prefix(findings, "unaddressed_na:") == []
    for performance in (
        f"Calibration warned [A:{VAL}#tests[cal.hl].status]. stab.psi.score failed. "
        "The coefficient sign check was run. Some results are not available.",
        f"Calibration warned [A:{VAL}#tests[cal.hl].status]. stab.psi.score failed.",
    ):
        findings = _with_prefix(
            run_prechecks(_ctx(_sections(performance=performance))), "unaddressed_na:"
        )
        assert [(f.check_key, f.severity, f.category, f.evidence) for f in findings] == [
            (
                "unaddressed_na:sens.signs",
                "high",
                "performance",
                ["performance", "[A:validation#tests[sens.signs].message]"],
            )
        ]


def test_reverify_catches_a_swapped_number_and_unknown_passage() -> None:
    assert _with_prefix(run_prechecks(_ctx(_sections())), "reverify:") == []
    swapped = _sections(performance=PERFORMANCE.replace("0.874", "0.875"))
    findings = _with_prefix(run_prechecks(_ctx(swapped)), "reverify:")
    assert [(f.check_key, f.severity, f.category, f.evidence) for f in findings] == [
        ("reverify:performance", "high", "documentation", ["performance"])
    ]
    assert 'ungrounded_numbers: ["0.875"]' in findings[0].description
    assert "unresolved_markers: []" in findings[0].description
    unknown = _sections(
        regulatory_context="SR 11-7 requires effective challenge [K:k_zzz].",
        data=f"There are 43405 rows [A:{'c' * 64}#n_rows].",
    )
    findings = _with_prefix(run_prechecks(_ctx(unknown)), "reverify:")
    assert [f.check_key for f in findings] == ["reverify:regulatory_context", "reverify:data"]
    assert 'foreign_passages: ["[K:k_zzz]"]' in findings[0].description
    assert f'unresolved_markers: ["[A:{"c" * 64}#n_rows]"]' in findings[1].description


def test_miscited_regulation() -> None:
    for text in (
        "SR 11-7 requires effective challenge [K:k_aaa].",
        "SR 26-2 superseded SR 11-7 [K:k_sr26].",
        "SR 11-7 is the earlier guidance [K:k_sr26].",
        "The model follows SR 11-7.",
    ):
        findings = run_prechecks(_ctx(_sections(regulatory_context=text)))
        assert _with_prefix(findings, "miscited_regulation:") == []
    bad = _sections(
        regulatory_context="SR 11-7 requires effective challenge [K:k_bbb]. "
        "IFRS 9 measures losses [K:k_bbb]. Basel III applies [K:k_zzz] [K:k_aaa]."
    )
    findings = _with_prefix(run_prechecks(_ctx(bad)), "miscited_regulation:")
    assert [(f.check_key, f.severity, f.category, f.evidence) for f in findings] == [
        ("miscited_regulation:regulatory_context:1", "medium", "documentation",
         ["regulatory_context", "[K:k_bbb]"]),
        ("miscited_regulation:regulatory_context:3", "medium", "documentation",
         ["regulatory_context", "[K:k_zzz]", "[K:k_aaa]"]),
    ]  # fmt: skip


def test_prechecks_order_and_keys_are_stable() -> None:
    sections = _sections(
        governance=BANNER,
        performance=PERFORMANCE.replace("0.874", "0.875").replace(
            "The score PSI test stab.psi.score failed. ", ""
        ),
        regulatory_context="SR 11-7 requires effective challenge [K:k_bbb].",
    )
    ctx = _ctx(sections)
    first, second = run_prechecks(ctx), run_prechecks(ctx)
    assert first == second
    assert [f.check_key for f in first] == [
        "missing_section:governance",
        "undiscussed_test:stab.psi.score",
        "reverify:performance",
        "miscited_regulation:regulatory_context:1",
    ]
