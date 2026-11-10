alter table runs add column pause_requested boolean not null default false;
create index runs_replay_of_idx on runs (replay_of);

create table settings (
    key text primary key,
    value jsonb not null,
    updated_at timestamptz not null default now(),
    updated_by text null
);

create table settings_audit (
    id bigserial primary key,
    key text not null,
    old_value jsonb null,
    new_value jsonb not null,
    updated_by text not null,
    ts timestamptz not null default now()
);
