alter table findings add column source text not null default 'review', add column check_key text null, add column verdict text null, add column closed_round int null;
