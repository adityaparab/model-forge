from modelforge_gateway.redaction import RedactionHit, scan, scan_messages
from modelforge_gateway.schemas import Message


def test_scan_finds_each_pattern_and_names_it() -> None:
    assert scan("mail me at jane.doe@example.com") == "email"
    assert scan("account GB82WEST12345698765432") == "iban"
    assert scan("obligor 123456789012") == "digit_run"
    assert scan("the Attr7 column", ["Attr7"]) == "restricted_term"
    assert scan("the attr7 column", ["Attr7"]) == "restricted_term"


def test_scan_ignores_artifact_ids_numbers_and_partial_terms() -> None:
    artifact = "a" * 20 + "1234567890123" + "b" * 31
    assert scan(f"[A:{artifact}#n_rows] 43405 rows, AUC 0.874, 4.8%, vintage 2026") is None
    assert scan("Attr70 and xAttr7 and Attr7_x", ["Attr7"]) is None
    assert scan("anything", [""]) is None
    assert scan("anything", ["   "]) is None


def test_scan_reports_first_pattern_in_order() -> None:
    assert scan("123456789012 jane@example.com", ["obligor"]) == "email"
    assert scan("obligor 123456789012", ["obligor"]) == "digit_run"


def test_scan_messages_reports_role_and_pattern() -> None:
    hit = scan_messages("safe", [Message(role="user", content="x@y.io")], [])
    assert hit == RedactionHit(pattern="email", role="user")
    assert scan_messages("safe", [Message(role="user", content="safe")], []) is None


def test_scan_messages_checks_system_first() -> None:
    hit = scan_messages("see Attr7", [Message(role="user", content="x@y.io")], ["Attr7"])
    assert hit == RedactionHit(pattern="restricted_term", role="system")


def test_digit_run_skips_decimals_and_citation_ids() -> None:
    assert scan("the PD is 0.123456789012") is None
    assert scan("see [K:k_123456789012]") is None
    assert scan("obligor 123456789012") == "digit_run"
    assert scan("ids 123456789012 and 0.5") == "digit_run"
