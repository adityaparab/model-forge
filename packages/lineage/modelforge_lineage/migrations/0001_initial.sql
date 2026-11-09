-- Lineage store: runs, queue, steps, hash-chained events, tool and LLM calls,
-- artifacts, approvals, findings, model inventory and prompts.

create table runs (
    id text primary key,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    requester text not null,
    status text not null,
    profile text not null,
    brief_hash text not null,
    brief_ref text not null,
    dataset_ref text not null,
    dataset_id text null,
    plan_ref text null,
    seed int not null,
    cost_usd numeric not null default 0,
    cost_cap_usd numeric not null default 10,
    trace_id text null,
    manifest_hash text null,
    replay_of text null references runs (id),
    error jsonb null
);

create table run_queue (
    run_id text primary key references runs (id),
    kind text not null check (kind in ('start', 'resume', 'replay')),
    enqueued_at timestamptz not null default now(),
    claimed_by text null,
    claimed_at timestamptz null
);

create table steps (
    id text primary key,
    run_id text not null references runs (id),
    node text not null,
    attempt int not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz null,
    status text not null check (status in ('running', 'succeeded', 'failed')),
    input_state_hash text not null,
    output_state_hash text null
);
create index steps_run_id_idx on steps (run_id);

create table events (
    id bigserial primary key,
    run_id text not null references runs (id),
    step_id text null,
    ts timestamptz not null default now(),
    type text not null,
    payload jsonb not null,
    prev_hash text not null,
    hash text not null,
    unique (run_id, hash)
);
create index events_run_id_id_idx on events (run_id, id);

create table tool_calls (
    id text primary key,
    step_id text not null references steps (id),
    server text not null,
    tool text not null,
    version text not null,
    input_hash text not null,
    output_hash text not null,
    duration_ms int not null,
    artifact_ids text[] not null
);
create index tool_calls_step_id_idx on tool_calls (step_id);

create table llm_calls (
    id text primary key,
    step_id text,
    purpose text,
    provider text,
    model text,
    prompt_name text,
    prompt_version text,
    prompt_hash text,
    input_tokens int,
    output_tokens int,
    cache_read_tokens int,
    cache_write_tokens int,
    cost_usd numeric,
    latency_ms int,
    stop_reason text,
    schema_valid boolean,
    raw_ref text
);

create table artifacts (
    id text not null,
    run_id text not null references runs (id),
    kind text not null,
    media_type text not null,
    size_bytes bigint not null,
    uri text not null,
    producer_step_id text null,
    schema_version text not null,
    created_at timestamptz not null default now(),
    primary key (id, run_id)
);
create index artifacts_run_id_idx on artifacts (run_id);

create table approvals (
    id text primary key,
    run_id text not null references runs (id),
    gate text not null,
    decision text not null,
    approver text not null,
    comment text not null,
    payload jsonb not null,
    plan_diff jsonb null,
    conditions text[] null,
    adjust_target text null,
    ts timestamptz not null default now()
);
create index approvals_run_id_gate_ts_idx on approvals (run_id, gate, ts);

create table findings (
    id text primary key,
    run_id text not null references runs (id),
    round int not null,
    severity text not null,
    category text not null,
    title text not null,
    description text not null,
    evidence jsonb not null,
    required_action text not null,
    status text not null,
    response text null,
    response_evidence jsonb null
);

create table model_inventory (
    model_id text primary key,
    run_id text not null references runs (id),
    name text not null,
    version int not null,
    owner text not null,
    intended_use text not null,
    tier int not null,
    status text not null,
    champion_ref text null,
    validation_summary jsonb null,
    approved_at timestamptz null,
    approver text null,
    conditions text[] null,
    next_review date null
);

create table prompts (
    name text not null,
    version int not null,
    hash text not null,
    path text not null,
    primary key (name, version)
);
