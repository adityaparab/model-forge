alter table llm_calls add column seq bigserial, add column ts timestamptz not null default now();
alter table runs add column rerun_llm boolean not null default false;
