"""Repository for the ``llm_calls`` table: one row per LLM request made within a step."""

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.models import LlmCallRecord

_COLUMNS = (
    "id, step_id, purpose, provider, model, prompt_name, prompt_version, prompt_hash,"
    " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,"
    " latency_ms, stop_reason, schema_valid, raw_ref"
)


def insert_llm_call(conn: Connection[DictRow], record: LlmCallRecord) -> None:
    conn.execute(
        f"insert into llm_calls ({_COLUMNS})"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            record.id,
            record.step_id,
            record.purpose,
            record.provider,
            record.model,
            record.prompt_name,
            str(record.prompt_version),
            record.prompt_hash,
            record.input_tokens,
            record.output_tokens,
            record.cache_read_tokens,
            record.cache_write_tokens,
            record.cost_usd,
            record.latency_ms,
            record.stop_reason,
            record.schema_valid,
            record.raw_ref,
        ),
    )


def list_llm_calls(conn: Connection[DictRow], run_id: str) -> list[LlmCallRecord]:
    columns = ", ".join(f"lc.{c.strip()}" for c in _COLUMNS.split(","))
    rows = conn.execute(
        f"select {columns} from llm_calls lc join steps s on s.id = lc.step_id"
        " where s.run_id = %s order by lc.seq",
        (run_id,),
    )
    return [
        LlmCallRecord.model_validate(
            {**r, "prompt_version": int(r["prompt_version"]), "cost_usd": float(r["cost_usd"])}
        )
        for r in rows
    ]
