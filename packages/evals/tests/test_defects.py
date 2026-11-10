"""The planted-defect suite: mutations of a rendered fake document, signatures and metrics."""

import json
from typing import Any

import pytest
from modelforge_evals.defects import (
    DefectCase,
    Mutation,
    MutationContext,
    Signature,
    apply_mutation,
    defect_metrics,
    load_defects,
    matches,
)
from modelforge_evals.probes import document_template, run_probe
from modelforge_knowledge.index import KnowledgeIndex
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.schemas import SectionSpec, TemplateSections
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import Finding, Manifest
from modelforge_orchestrator.sections import split_sections
from pydantic import ValidationError

from .conftest import REPO

CASES = load_defects(REPO / "evals/goldens/defects.yaml")

UNDETECTED_UNDER_FAKE = {"missing_limitation"}
"""Applicable on the synthetic document but not detected by the ``fake`` profile.

The fake review raises its dataset-proxy finding only when the plan's target event names a
proxy, and the synthetic brief's target is "synthetic default event"; the Polish brief's
target names one, so that case is detected there.
"""


def _finding(**overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "id": "F-1",
        "round": 1,
        "severity": "medium",
        "category": "documentation",
        "title": "A finding",
        "description": "A description.",
        "evidence": ["performance"],
        "required_action": "Act.",
    }
    fields.update(overrides)
    return Finding.model_validate(fields)


def _case(case_id: str, expect: Signature) -> DefectCase:
    mutation = Mutation(kind="append_sentence", section="performance", text="x.")
    return DefectCase(id=case_id, mutation=mutation, expect=expect)


Base = tuple[str, TemplateSections, MutationContext]


@pytest.fixture
def base(database: Database, store: ArtifactStore, deps: Deps, completed_run_id: str) -> Base:
    """The completed fake run's rendered document, the template and the mutation context."""
    with database.connection() as conn:
        manifest_hash = runs.get_run(conn, completed_run_id).manifest_hash
    assert manifest_hash is not None
    refs = store.get_model(manifest_hash, Manifest).refs
    ctx = MutationContext(
        validation=json.loads(store.get(refs["validation"].id)),
        index=store.get_model(refs["knowledge_index"].id, KnowledgeIndex),
    )
    markdown = store.get(refs["document_md"].id).decode("utf-8")
    return markdown, document_template(deps), ctx


def test_the_suite_holds_the_six_spec_cases() -> None:
    assert [c.id for c in CASES] == [
        "removed_section",
        "swapped_number",
        "claimed_absent_test",
        "miscited_regulation",
        "undiscussed_breach",
        "missing_limitation",
    ]


def test_mutation_requires_the_field_its_kind_reads() -> None:
    for kind in ("append_sentence", "remove_sentences_containing"):
        with pytest.raises(ValidationError, match="requires text"):
            Mutation(kind=kind, section="performance")
        assert Mutation(kind=kind, section="performance", text="x.").text == "x."
    with pytest.raises(ValidationError, match="requires doc_id"):
        Mutation(kind="swap_citation", section="regulatory_context")
    assert Mutation(kind="swap_citation", section="regulatory_context", doc_id="ifrs9-ecl").doc_id
    assert Mutation(kind="remove_section", section="monitoring").text is None


def test_remove_test_sentences_over_a_hand_built_document() -> None:
    template = TemplateSections(
        template_id="t",
        sections=[
            SectionSpec(id=i, title=t, required=True, artifact_kinds=[], guidance="g")
            for i, t in (("performance", "Performance"), ("limitations", "Limitations"))
        ],
    )
    marked = f"Calibration warned [A:{'e' * 64}#tests[cal.hl].status]."
    markdown = (
        "# Document\n\n## 1. Performance\n\n"
        f"The AUC is high. {marked}\n\n"
        f"## 2. Limitations\n\nThe data is a proxy. {marked}\n"
    )
    index = KnowledgeIndex(built_from={}, documents=[], chunks=[])
    mutation = Mutation(kind="remove_test_sentences", section="performance")

    def ctx(status: str) -> MutationContext:
        tests = [{"test_id": "cal.hl", "status": status}, {"test_id": "disc.auc", "status": "pass"}]
        return MutationContext(validation={"tests": tests}, index=index)

    mutated = apply_mutation(markdown, template, mutation, ctx("warn"))
    assert mutated == markdown.replace(f"The AUC is high. {marked}", "The AUC is high.")
    assert apply_mutation(markdown, template, mutation, ctx("pass")) is None


def test_swap_digit_skips_marker_sentences_without_a_number() -> None:
    template = TemplateSections(
        template_id="t",
        sections=[
            SectionSpec(
                id="performance",
                title="Performance",
                required=True,
                artifact_kinds=[],
                guidance="g",
            )
        ],
    )
    first = f"The overall validation outcome is fail [A:{'e' * 64}#overall]."
    second = f"The AUC is 0.84 [A:{'e' * 64}#tests[disc.auc].value]."
    markdown = f"# Document\n\n## 1. Performance\n\n{first} {second}\n"
    index = KnowledgeIndex(built_from={}, documents=[], chunks=[])
    ctx = MutationContext(validation={"tests": []}, index=index)
    mutation = Mutation(kind="swap_digit", section="performance")

    mutated = apply_mutation(markdown, template, mutation, ctx)
    assert mutated == markdown.replace("0.84", "0.85")
    assert first in (mutated or "")


@pytest.mark.db
def test_most_mutations_apply_to_the_synthetic_document(base: Base) -> None:
    markdown, template, ctx = base
    mutated = {c.id: apply_mutation(markdown, template, c.mutation, ctx) for c in CASES}
    assert all(m is None or m != markdown for m in mutated.values())
    assert sum(m is not None for m in mutated.values()) >= 4
    # Every validation test of the synthetic run passes, so no failing test is cited.
    assert mutated["undiscussed_breach"] is None
    before = split_sections(markdown, template)
    for case_id, document in mutated.items():
        if document is None:
            continue
        after = split_sections(document, template)
        changed = {s for s in before if before[s] != after.get(s)}
        section = next(c for c in CASES if c.id == case_id).mutation.section
        assert changed == {section}, case_id
    assert "monitoring" not in split_sections(mutated["removed_section"] or "", template)
    swapped = split_sections(mutated["swapped_number"] or "", template)["performance"]
    assert "0.7392 [A:" in swapped and "0.7391 [A:" not in swapped


@pytest.mark.db
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_each_mutation_plants_a_detectable_defect(
    case: DefectCase, deps: Deps, completed_run_id: str
) -> None:
    result = run_probe(deps, completed_run_id, case, document_template(deps))
    detected = any(matches(f, case.expect) for f in result.findings)
    if case.id in UNDETECTED_UNDER_FAKE:
        # See `UNDETECTED_UNDER_FAKE`: the mutation applies, the fake review cannot see it.
        assert result.applicable is True and not detected
    else:
        assert result.applicable is False or detected, [
            (f.check_key, f.category, f.title) for f in result.findings
        ]


@pytest.mark.db
def test_inapplicable_defect_case_is_excluded_from_recall(base: Base) -> None:
    markdown, template, ctx = base
    absent = Mutation(
        kind="remove_sentences_containing", section="limitations", text="zzz-not-there"
    )
    assert apply_mutation(markdown, template, absent, ctx) is None
    nowhere = Mutation(kind="remove_section", section="no_such_section")
    assert apply_mutation(markdown, template, nowhere, ctx) is None
    hit = _case("hit", Signature(check_key="reverify:*"))
    miss = _case("miss", Signature(category="data", section="limitations"))
    gone = DefectCase(id="gone", mutation=absent, expect=Signature(check_key="x"))
    recall, mean, _ = defect_metrics(
        [hit, miss, gone],
        {
            "hit": (True, [_finding(check_key="reverify:performance", source="precheck")]),
            "miss": (True, []),
            "gone": (None, []),
        },
        [],
    )
    assert recall == {"hit": 1.0, "miss": 0.0, "gone": None}
    assert mean == 0.5


def test_matches_by_glob_and_by_category_section() -> None:
    precheck = _finding(check_key="miscited_regulation:regulatory_context:2", source="precheck")
    assert matches(precheck, Signature(check_key="miscited_regulation:regulatory_context:*"))
    assert not matches(precheck, Signature(check_key="miscited_regulation:data:*"))
    review = _finding(category="data", evidence=["limitations", "[A:abc#target.event]"])
    assert matches(review, Signature(category="data", section="limitations"))
    assert not matches(review, Signature(category="data", section="performance"))
    assert not matches(review, Signature(category="performance", section="limitations"))
    # A signature with a check key matches by the key alone, so a review finding never does.
    assert not matches(
        review, Signature(check_key="reverify:*", category="data", section="limitations")
    )


def test_precision_counts_unexpected_findings_only() -> None:
    case = _case("swapped_number", Signature(check_key="reverify:performance"))
    expected = _finding(id="F-1", check_key="reverify:performance", source="precheck")
    known = _finding(id="F-2", category="monitoring", title="Monitoring thresholds")
    unexpected = _finding(id="F-3", category="governance", title="Something new")
    baseline = [_finding(id="F-9", category="monitoring", title="Monitoring thresholds")]
    recall, mean, precision = defect_metrics(
        [case], {"swapped_number": (True, [expected, known, unexpected])}, baseline
    )
    assert recall == {"swapped_number": 1.0} and mean == 1.0
    assert precision == 0.5
    _, _, none = defect_metrics([case], {"swapped_number": (None, [unexpected])}, baseline)
    assert none is None  # an inapplicable probe's findings do not count
