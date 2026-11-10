from typing import Any

import pytest
from modelforge_mcp.schemas import DocumentSection, GroundingReport
from modelforge_orchestrator.grounding import (
    check_section,
    expand_aliases,
    matches,
    numbers_in,
    round_sig,
    strip_non_claims,
)


def test_numbers_in_and_non_claims() -> None:
    tokens = numbers_in(
        strip_non_claims(
            "Under SR 11-7 (April 2011) the AUC is 0.874 [A:x#y] and 4.8% of 43,405 rows, "
            "13 features, column f_12, `code 99`."
        )
    )
    assert [(t.text, t.percent, t.is_integer, t.sig_figs) for t in tokens] == [
        ("0.874", False, False, 3),
        ("4.8%", True, False, 2),
        ("43,405", False, True, 5),
        ("13", False, True, 2),
    ]


def test_numbers_in_keeps_the_sign() -> None:
    tokens = numbers_in("coefficient -2.95 [A:x#y]")
    assert [(t.text, t.value) for t in tokens] == [("-2.95", -2.95)]


def test_sig_figs_and_round_sig() -> None:
    assert [numbers_in(t)[0].sig_figs for t in ("0.05", "1.0", "4.8")] == [1, 2, 2]
    assert round_sig(0.87414, 3) == 0.874
    assert round_sig(43405.0, 2) == 43000.0
    assert round_sig(0.0, 3) == 0.0


@pytest.mark.parametrize(
    "text,value,expected",
    [
        ("0.874", 0.8741, True),
        ("0.87", 0.8741, True),
        ("0.88", 0.8741, False),
        ("87.4%", 0.8741, True),
        ("5%", 0.0482, True),
        ("13", 13, True),
        ("13", 13.0, True),
        ("13", 12.6, False),
        ("43,405", 43405, True),
        ("0.874", "NaN", False),
        ("1", True, False),
        ("0.874", None, False),
        ("5", "1 to 5 years", True),
        ("15", "1 to 5 years", False),
        ("5", [1, 5], True),
        ("5", [{"a": 5}], False),
        ("-2.95", -2.951, True),
        ("2.95", -2.951, False),
    ],
)
def test_matches(text: str, value: Any, expected: bool) -> None:
    assert matches(numbers_in(text)[0], value) is expected


def test_expand_aliases_and_unknown() -> None:
    body, unknown = expand_aliases("x [A:profile#n_rows] y [A:nope#z]", {"profile": "a" * 64})
    assert body == f"x [A:{'a' * 64}#n_rows] y [A:nope#z]" and unknown == ["[A:nope#z]"]


def test_expand_aliases_leaves_expanded_markers_alone() -> None:
    marker = f"[A:{'b' * 64}#n_rows]"
    assert expand_aliases(marker, {"profile": "a" * 64}) == (marker, [])


def _loader(art: dict[str, Any], ida: str) -> Any:
    def load(i: str) -> Any:
        if i != ida:
            raise KeyError(i)
        return art

    return load


def test_check_section_reports_each_failure_class() -> None:
    art = {"n_rows": 43405, "by_split": [{"split": "oot", "auc": 0.874}], "horizon": "1 to 5 years"}
    ida = "a" * 64
    load = _loader(art, ida)
    ok = check_section(
        f"It has 43405 rows [A:{ida}#n_rows]. AUC 0.874 [A:{ida}#by_split.oot.auc]. "
        f"Horizon 1 to 5 years [A:{ida}#horizon]. SR 11-7 requires effective challenge [K:k_1].",
        load_artifact=load,
        retrieved={"k_1"},
    )
    assert ok.ok and ok == GroundingReport(ok=True)
    bad = check_section(
        f"It has 999 rows [A:{ida}#n_rows]. AUC 0.874 [A:{ida}#by_split.train.auc]. "
        f"Missing [A:{'b' * 64}#n_rows]. SR 11-7 says so. Other [K:k_9]. [K:]",
        load_artifact=load,
        retrieved={"k_1"},
    )
    assert bad.ungrounded_numbers == ["999", "0.874"] and bad.unresolved_markers == [
        f"[A:{ida}#by_split.train.auc]",
        f"[A:{'b' * 64}#n_rows]",
        "[K:]",
    ]
    assert bad.uncited_regulatory_sentences == ["SR 11-7 says so."]
    assert bad.foreign_passages == ["[K:k_9]"] and not bad.ok


def test_check_section_malformed_a_markers_and_paragraphs() -> None:
    ida = "a" * 64
    load = _loader({"n_rows": 5, "coef": -2.951}, ida)
    report = check_section(
        f"Short id [A:abc#n_rows]. Empty path [A:{ida}#]. No hash [A:{ida}]\n\n"
        f"- coefficient -2.95 [A:{ida}#coef]\n- 7 rows [A:{ida}#n_rows]",
        load_artifact=load,
        retrieved=set(),
    )
    assert report.unresolved_markers == ["[A:abc#n_rows]", f"[A:{ida}#]", f"[A:{ida}]"]
    assert report.ungrounded_numbers == ["7"]


def test_document_section_grounding_is_optional() -> None:
    section = DocumentSection(section_id="data", body_md="x")
    assert section.grounding is None
    assert DocumentSection(section_id="data", body_md="x", grounding=GroundingReport(ok=True))
