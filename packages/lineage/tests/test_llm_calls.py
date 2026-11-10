import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.ids import new_id
from modelforge_lineage.llm_calls import insert_llm_call, list_llm_calls
from modelforge_lineage.models import LlmCallRecord
from modelforge_lineage.steps import start_step
from modelforge_lineage.testing import create_test_run as _run


def _record(step_id: str, version: int, raw_ref: str | None) -> LlmCallRecord:
    return LlmCallRecord(
        id=new_id("llm"),
        step_id=step_id,
        purpose="scope",
        provider="anthropic",
        model="claude-test",
        prompt_name="scope_brief",
        prompt_version=version,
        prompt_hash="p" * 64,
        input_tokens=120,
        output_tokens=40,
        cache_read_tokens=0,
        cache_write_tokens=100,
        cost_usd=0.0125,
        latency_ms=850,
        stop_reason="end_turn" if raw_ref else None,
        schema_valid=True,
        raw_ref=raw_ref,
    )


@pytest.mark.db
def test_insert_and_list_llm_calls_in_order(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
        _run(conn, "run_b")
        step = start_step(conn, "run_a", "scope", "h0")
        other = start_step(conn, "run_b", "scope", "h0")
        second = _record(step.id, 2, "r" * 64)
        first = _record(step.id, 1, None)
        insert_llm_call(conn, second)
        insert_llm_call(conn, _record(other.id, 1, None))
        insert_llm_call(conn, first)
    with db.connection() as conn:
        calls = list_llm_calls(conn, "run_a")
    assert calls == [second, first]
    assert [c.prompt_version for c in calls] == [2, 1]
    assert isinstance(calls[0].cost_usd, float) and calls[1].raw_ref is None
