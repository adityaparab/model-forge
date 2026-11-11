from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import Submit

API = "/api/v1"


def _get(client: TestClient, path: str) -> Any:
    response = client.get(f"{API}{path}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.db
def test_findings_grouped(client: TestClient, completed_run: str, submit: Submit) -> None:
    body = _get(client, f"/runs/{completed_run}/findings")
    detail = _get(client, f"/runs/{completed_run}")
    findings, responses, summary = body["findings"], body["responses"], body["summary"]

    assert findings and len(findings) == summary["n_findings"]
    for row in findings:
        assert row["id"].startswith("F-")
        assert row["row_id"] == f"{completed_run}/{row['id']}"
        assert row["run_id"] == completed_run
        assert isinstance(row["evidence"], list)
    assert sum(summary["by_severity"].values()) == summary["n_findings"]
    assert sum(summary["by_status"].values()) == summary["n_findings"]
    assert sum(summary["by_source"].values()) == summary["n_findings"]
    ref_summary = detail["findings_summary"]
    for key in ("n_findings", "by_severity", "by_status", "by_source", "max_severity"):
        assert summary[key] == ref_summary[key]
    assert summary["max_open_severity"] == ref_summary["max_open_severity"]

    assert responses and all(r["round"] >= 1 for r in responses)
    assert {r["finding_id"] for r in responses} <= {row["id"] for row in findings}
    assert summary["challenge_rounds"] >= 1
    assert summary["challenge_rounds"] == detail["counters"]["challenge_rounds"]
    assert summary["remediation_rounds"] == detail["counters"]["remediation_rounds"]
    regenerated = {s for r in responses for s in r["sections_regenerated"]}
    assert set(summary["sections_regenerated"]) == regenerated
    document = _get(client, f"/runs/{completed_run}/document")
    assert regenerated <= {s["section_id"] for s in document["sections"]}

    queued = submit()
    response = client.get(f"{API}/runs/{queued}/findings")
    assert response.status_code == 409 and response.json()["error"]["kind"] == "not_ready"


@pytest.mark.db
def test_findings_resolve_evidence_citations(client: TestClient, completed_run: str) -> None:
    body = _get(client, f"/runs/{completed_run}/findings")
    validation_ref = _get(client, f"/runs/{completed_run}")["refs"]["validation"]["id"]
    checked = False
    for row in body["findings"]:
        markers = [
            m for m in [*row["evidence"], *(row["response_evidence"] or [])] if m.startswith("[")
        ]
        assert [c["marker"] for c in row["citations"]] == list(dict.fromkeys(markers))
        for citation in row["citations"]:
            if citation["artifact_id"] == validation_ref and citation["path"].startswith("tests["):
                assert citation["resolved"] is True, citation
                assert citation["artifact_kind"] == "validation_report"
                assert citation["value"] is not None
                checked = True
    assert checked, "no validation test-status evidence among the findings"
