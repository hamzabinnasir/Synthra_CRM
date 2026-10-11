-- Tenant database, migration 0002: media storage and dashboard query indexes.
-- Inbound customer media (images, audio, video, documents) is copied here the moment it arrives, because
-- the temporary URLs Meta provides expire within minutes. Messages reference it as "media:<uuid>" and the
-- dashboard serves it through the authenticated /api/media endpoint. Idempotent.

create table if not exists crm.media (
  id          uuid primary key default gen_random_uuid(),
  mime_type   text not null,
  filename    text,
  size_bytes  integer not null check (size_bytes >= 0),
  data        bytea not null,
  created_at  timestamptz not null default now()
);
alter table crm.media enable row level security;

create index if not exists comments_post_idx on crm.comments (platform, post_id);
create index if not exists messages_media_idx on crm.messages (media_url) where media_url is not null;
create index if not exists customers_handoff_idx on crm.customers (handoff_requested) where handoff_requested;

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on all tables in schema crm from %I', r);
    end if;
  end loop;
end $$;
