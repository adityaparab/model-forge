"""Repository for graph steps, the MCP tool calls made within them, and artifact rows."""

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.ids import new_id
from modelforge_lineage.models import (
    ArtifactRef,
    ArtifactRow,
    StepRecord,
    StepStatus,
    ToolCallRecord,
)


def start_step(
    conn: Connection[DictRow], run_id: str, node: str, input_state_hash: str
) -> StepRecord:
    count = conn.execute(
        "select count(*) as n from steps where run_id = %s and node = %s", (run_id, node)
    ).fetchone()
    assert count is not None
    row = conn.execute(
        "insert into steps (id, run_id, node, attempt, started_at, status, input_state_hash)"
        " values (%s, %s, %s, %s, clock_timestamp(), 'running', %s) returning *",
        (new_id("step"), run_id, node, 1 + count["n"], input_state_hash),
    ).fetchone()
    assert row is not None
    return StepRecord.model_validate(row)


def finish_step(
    conn: Connection[DictRow],
    step_id: str,
    *,
    status: StepStatus,
    output_state_hash: str | None,
) -> None:
    conn.execute(
        "update steps set status = %s, output_state_hash = %s, finished_at = clock_timestamp()"
        " where id = %s",
        (status, output_state_hash, step_id),
    )


def list_steps(conn: Connection[DictRow], run_id: str) -> list[StepRecord]:
    rows = conn.execute(
        "select * from steps where run_id = %s order by started_at, attempt, id", (run_id,)
    )
    return [StepRecord.model_validate(r) for r in rows]


def insert_tool_call(conn: Connection[DictRow], record: ToolCallRecord) -> None:
    conn.execute(
        "insert into tool_calls (id, step_id, server, tool, version, input_hash, output_hash,"
        " duration_ms, artifact_ids) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            record.id,
            record.step_id,
            record.server,
            record.tool,
            record.version,
            record.input_hash,
            record.output_hash,
            record.duration_ms,
            record.artifact_ids,
        ),
    )


def list_tool_calls(conn: Connection[DictRow], run_id: str) -> list[ToolCallRecord]:
    rows = conn.execute(
        "select tc.id, tc.step_id, tc.server, tc.tool, tc.version, tc.input_hash,"
        " tc.output_hash, tc.duration_ms, tc.artifact_ids"
        " from tool_calls tc join steps s on s.id = tc.step_id"
        " where s.run_id = %s order by tc.seq",
        (run_id,),
    )
    return [ToolCallRecord.model_validate(r) for r in rows]


def insert_artifact(
    conn: Connection[DictRow],
    *,
    run_id: str,
    ref: ArtifactRef,
    uri: str,
    producer_step_id: str | None,
    schema_version: str = "1",
) -> None:
    conn.execute(
        "insert into artifacts (id, run_id, kind, media_type, size_bytes, uri,"
        " producer_step_id, schema_version) values (%s, %s, %s, %s, %s, %s, %s, %s)"
        " on conflict (id, run_id) do nothing",
        (
            ref.id,
            run_id,
            ref.kind,
            ref.media_type,
            ref.size_bytes,
            uri,
            producer_step_id,
            schema_version,
        ),
    )


def list_artifacts(conn: Connection[DictRow], run_id: str) -> list[ArtifactRow]:
    rows = conn.execute(
        "select id, run_id, kind, media_type, size_bytes, uri, producer_step_id, schema_version"
        " from artifacts where run_id = %s order by created_at, id",
        (run_id,),
    )
    return [ArtifactRow.model_validate(r) for r in rows]
