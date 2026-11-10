"""The challenger's deterministic pre-checks (spec 3.2).

Six pure checks over the rendered development document, run before any model call. Each failing
check is a `PrecheckFinding` with a stable ``check_key``, so a later round closes the finding when
the check no longer fires; keys carry ids and ordinals only, never text that a revision changes.
The challenger node builds the `PrecheckContext` from lineage; nothing here touches the database,
the gateway or a tool.
"""

import json
import re
from collections.abc import Callable, Iterator
from typing import Any, Literal

from modelforge_core.schemas import StrictModel, ValidationTest
from modelforge_mcp.schemas import CompletenessReport, TemplateSections
from modelforge_mcp.servers.doc_renderer import BANNER, MARKER
from pydantic import ConfigDict

from modelforge_orchestrator.excerpts import excerpt
from modelforge_orchestrator.grounding import REGULATION_NAME, check_section
from modelforge_orchestrator.schemas import FindingCategory
from modelforge_orchestrator.sections import section_sentences

__all__ = [
    "BANNERED_SECTION",
    "MISCITED_REGULATION",
    "MISSING_SECTION",
    "REGULATION_DOCS",
    "REVERIFY",
    "UNADDRESSED_NA",
    "UNDISCUSSED_TEST",
    "PrecheckContext",
    "PrecheckFinding",
    "run_prechecks",
]

MISSING_SECTION = "missing_section"
BANNERED_SECTION = "bannered_section"
UNDISCUSSED_TEST = "undiscussed_test"
UNADDRESSED_NA = "unaddressed_na"
REVERIFY = "reverify"
MISCITED_REGULATION = "miscited_regulation"

REGULATION_DOCS: dict[str, str] = {
    "SR 11-7": "sr11-7",
    "SR 26-2": "sr26-2",
    "OCC": "occ-2026-13",
    "CECL": "cecl-asc-326",
    "ASC 326": "cecl-asc-326",
    "IFRS 9": "ifrs9-ecl",
    "Basel": "basel-irb",
}
"""Regulation name prefix to the knowledge-base ``doc_id`` that carries it; first match wins."""

VALIDATION_ALIAS = "validation"
"""The alias evidence markers use; the challenger node expands it to the validation id."""
_NOT_APPLICABLE = ("not applicable", "not_applicable", "not available")
_CODE_SPAN = re.compile(r"`[^`]*`")

Severity = Literal["critical", "high", "medium", "low"]


class PrecheckFinding(StrictModel):
    check_key: str
    severity: Severity
    category: FindingCategory
    title: str
    description: str
    evidence: list[str]
    required_action: str


class PrecheckContext(StrictModel):
    """Everything the pre-checks read: section bodies by template id, the template, the
    completeness report, the validation excerpt and its id, the run's artifact kinds with a raw
    loader by id, and the knowledge index's passages as citation id to ``(doc_id, text)``."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    sections: dict[str, str]
    template: TemplateSections
    completeness: CompletenessReport
    validation: dict[str, Any]
    validation_id: str
    artifact_kinds: dict[str, str]
    load_artifact: Callable[[str], Any]
    passages: dict[str, tuple[str, str]]


def _ordered_bodies(ctx: PrecheckContext) -> Iterator[tuple[str, str]]:
    """``(section_id, body)`` for each present section, in template order."""
    for spec in ctx.template.sections:
        if spec.id in ctx.sections:
            yield spec.id, ctx.sections[spec.id]


def _claims(body: str) -> str:
    """``body`` without the banner line and with inline code spans blanked: the document agent
    quotes a section's failures in code spans under the banner, and a quote is not a claim."""
    lines = [line for line in body.splitlines() if line.strip() != BANNER]
    return _CODE_SPAN.sub(" ", "\n".join(lines))


def _tests(ctx: PrecheckContext) -> list[ValidationTest]:
    return [ValidationTest.model_validate(t) for t in ctx.validation.get("tests") or []]


def _names_test(text: str, test: ValidationTest) -> bool:
    lowered = text.lower()
    return test.name.lower() in lowered or test.test_id.lower() in lowered


def _test_marker(test_id: str, field: str) -> str:
    return f"[A:{VALIDATION_ALIAS}#tests[{test_id}].{field}]"


def check_missing_sections(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """Check 1: required sections absent or only the banner, and the completeness report's."""
    flagged = {
        spec.id
        for spec in ctx.template.sections
        if spec.required
        and (spec.id not in ctx.sections or ctx.sections[spec.id].strip() == BANNER)
    }
    flagged.update(ctx.completeness.missing_sections)
    order = [spec.id for spec in ctx.template.sections]
    order += [i for i in ctx.completeness.missing_sections if i not in order]
    return [
        PrecheckFinding(
            check_key=f"{MISSING_SECTION}:{section_id}",
            severity="critical",
            category="documentation",
            title=f"Required section `{section_id}` is missing",
            description=(
                f"The required template section `{section_id}` is absent from the document "
                "or carries only the author-attention banner."
            ),
            evidence=[section_id],
            required_action=f"Write the `{section_id}` section from its source artifacts.",
        )
        for section_id in dict.fromkeys(order)
        if section_id in flagged
    ]


def check_bannered_sections(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """Check 2: no section starts with the author-attention banner above further content.

    A body that is only the banner is a missing section (check 1), reported once there."""
    return [
        PrecheckFinding(
            check_key=f"{BANNERED_SECTION}:{section_id}",
            severity="high",
            category="documentation",
            title=f"Section `{section_id}` requires author attention",
            description=f"The section `{section_id}` carries the author-attention banner.",
            evidence=[section_id],
            required_action=f"Supply the `{section_id}` section and remove the banner.",
        )
        for section_id, body in _ordered_bodies(ctx)
        if body.strip().startswith(BANNER) and body.strip() != BANNER
    ]


def _cites_test(ctx: PrecheckContext, test_id: str) -> bool:
    needle = f"tests[{test_id}]"
    return any(
        match.group(1) == "A"
        and match.group(2).startswith(f"{ctx.validation_id}#")
        and needle in match.group(2)
        for body in ctx.sections.values()
        for match in MARKER.finditer(body)
    )


def check_undiscussed_tests(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """Check 3: every ``fail`` or ``warn`` test is cited by marker or named in the document."""
    findings: list[PrecheckFinding] = []
    for test in _tests(ctx):
        if test.status not in ("fail", "warn"):
            continue
        if _cites_test(ctx, test.test_id) or any(
            _names_test(body, test) for body in ctx.sections.values()
        ):
            continue
        findings.append(
            PrecheckFinding(
                check_key=f"{UNDISCUSSED_TEST}:{test.test_id}",
                severity="high" if test.status == "fail" else "medium",
                category="performance",
                title=f"Validation test `{test.test_id}` is not discussed",
                description=(
                    f"The validation test `{test.test_id}` ({test.name}) has status "
                    f"`{test.status}` but the document neither cites nor names it."
                ),
                evidence=["performance", _test_marker(test.test_id, "status")],
                required_action=(
                    f"Discuss the `{test.status}` outcome of `{test.test_id}` in the performance "
                    "section, citing the validation report."
                ),
            )
        )
    return findings


def check_unaddressed_not_applicable(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """Check 4: a ``not_applicable`` test is named in a sentence that says so."""
    sentences = [s for _, body in _ordered_bodies(ctx) for s in section_sentences(body)]
    findings: list[PrecheckFinding] = []
    for test in _tests(ctx):
        if test.status != "not_applicable":
            continue
        if any(
            _names_test(s, test) and any(p in s.lower() for p in _NOT_APPLICABLE) for s in sentences
        ):
            continue
        findings.append(
            PrecheckFinding(
                check_key=f"{UNADDRESSED_NA}:{test.test_id}",
                severity="high",
                category="performance",
                title=f"Not-applicable test `{test.test_id}` is not addressed",
                description=(
                    f"The validation test `{test.test_id}` ({test.name}) is not applicable "
                    f"({test.message}), but no sentence names it as not applicable."
                ),
                evidence=["performance", _test_marker(test.test_id, "message")],
                required_action=(
                    f"State in the document that `{test.test_id}` is not applicable and why."
                ),
            )
        )
    return findings


def check_reverify(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """Check 5: the grounding check, re-run over each section against sealed artifacts."""

    def load(artifact_id: str) -> Any:
        if artifact_id not in ctx.artifact_kinds:
            raise KeyError(artifact_id)
        return excerpt(ctx.artifact_kinds[artifact_id], ctx.load_artifact(artifact_id))

    findings: list[PrecheckFinding] = []
    for section_id, body in _ordered_bodies(ctx):
        report = check_section(_claims(body), load_artifact=load, retrieved=set(ctx.passages))
        if report.ok:
            continue
        failures = {
            "ungrounded_numbers": report.ungrounded_numbers,
            "unresolved_markers": report.unresolved_markers,
            "uncited_regulatory_sentences": report.uncited_regulatory_sentences,
            "foreign_passages": report.foreign_passages,
        }
        listed = "; ".join(f"{name}: {json.dumps(items)}" for name, items in failures.items())
        findings.append(
            PrecheckFinding(
                check_key=f"{REVERIFY}:{section_id}",
                severity="high",
                category="documentation",
                title=f"Section `{section_id}` fails re-verification",
                description=(
                    f"Re-verifying `{section_id}` against the sealed artifacts and the knowledge "
                    f"index failed. {listed}."
                ),
                evidence=[section_id],
                required_action=(
                    f"Correct `{section_id}` so every number matches a cited artifact value, "
                    "every marker resolves and every regulatory statement cites a known passage."
                ),
            )
        )
    return findings


def _regulation_doc(name: str) -> str | None:
    for prefix, doc_id in REGULATION_DOCS.items():
        if name == prefix or name.startswith(prefix + " "):
            return doc_id
    return None


def _consistent(ctx: PrecheckContext, citation_id: str, names: list[str]) -> bool:
    if citation_id not in ctx.passages:
        return False
    doc_id, text = ctx.passages[citation_id]
    if doc_id in {_regulation_doc(name) for name in names}:
        return True
    lowered = text.lower()
    return any(name.lower() in lowered for name in names)


def check_miscited_regulations(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """Check 6: a sentence naming a regulation cites passages of that regulation."""
    findings: list[PrecheckFinding] = []
    for section_id, body in _ordered_bodies(ctx):
        for n, sentence in enumerate(section_sentences(_claims(body)), start=1):
            names = [
                " ".join(m.group(0).split())
                for m in REGULATION_NAME.finditer(MARKER.sub(" ", sentence))
            ]
            cited = [m.group(2) for m in MARKER.finditer(sentence) if m.group(1) == "K"]
            if not names or not cited:
                continue
            wrong = [c for c in cited if not _consistent(ctx, c, names)]
            if not wrong:
                continue
            markers = [f"[K:{c}]" for c in wrong]
            findings.append(
                PrecheckFinding(
                    check_key=f"{MISCITED_REGULATION}:{section_id}:{n}",
                    severity="medium",
                    category="documentation",
                    title=f"Regulatory citation mismatch in `{section_id}`",
                    description=(
                        f"Sentence {n} of `{section_id}` names {', '.join(names)} but cites "
                        f"{', '.join(markers)}, which is not a passage of that regulation."
                    ),
                    evidence=[section_id, *markers],
                    required_action=(
                        "Cite a knowledge passage from the regulation the sentence names."
                    ),
                )
            )
    return findings


def run_prechecks(ctx: PrecheckContext) -> list[PrecheckFinding]:
    """All six checks' findings, in the spec table's order."""
    return [
        *check_missing_sections(ctx),
        *check_bannered_sections(ctx),
        *check_undiscussed_tests(ctx),
        *check_unaddressed_not_applicable(ctx),
        *check_reverify(ctx),
        *check_miscited_regulations(ctx),
    ]
