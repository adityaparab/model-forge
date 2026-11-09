import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import ArtifactRef, ToolCallRecord
from modelforge_lineage.steps import (
    finish_step,
    insert_artifact,
    insert_tool_call,
    list_artifacts,
    list_steps,
    list_tool_calls,
    start_step,
)
from modelforge_lineage.testing import create_test_run as _run


@pytest.mark.db
def test_steps_attempts_tool_calls_and_artifacts(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
        s1 = start_step(conn, "run_a", "profile", "h0")
        finish_step(conn, s1.id, status="failed", output_state_hash=None)
        s2 = start_step(conn, "run_a", "profile", "h0")
        insert_tool_call(
            conn,
            ToolCallRecord(
                id=new_id("tc"),
                step_id=s2.id,
                server="data-profiler",
                tool="profile_dataset",
                version="1.0.0",
                input_hash="i",
                output_hash="o",
                duration_ms=5,
                artifact_ids=["a" * 64],
            ),
        )
        ref = ArtifactRef(id="a" * 64, kind="profile", media_type="application/json", size_bytes=3)
        insert_artifact(conn, run_id="run_a", ref=ref, uri="file:///x", producer_step_id=s2.id)
        insert_artifact(conn, run_id="run_a", ref=ref, uri="file:///x", producer_step_id=s2.id)
        finish_step(conn, s2.id, status="succeeded", output_state_hash="h1")
    with db.connection() as conn:
        steps = list_steps(conn, "run_a")
        calls = list_tool_calls(conn, "run_a")
        arts = list_artifacts(conn, "run_a")
    assert [s.attempt for s in steps] == [1, 2]
    assert steps[1].status == "succeeded" and steps[1].output_state_hash == "h1"
    assert len(calls) == 1 and calls[0].artifact_ids == ["a" * 64] and len(arts) == 1
