-- Control-plane migration 0004: per-workspace daily AI call counter.
-- Each workspace brings its own LLM key; this counter only protects that key (and the owner's bill)
-- from loops and abuse. The daily cap comes from CRM_LLM_DAILY_CALLS. Days are UTC.

create table llm_usage (
  tenant_id  uuid not null references tenants(id) on delete cascade,
  day        date not null,
  calls      integer not null default 0 check (calls >= 0),
  primary key (tenant_id, day)
);
create index llm_usage_day_idx on llm_usage (day);
