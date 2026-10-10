-- Tenant database, migration 0001: conversations, comments, knowledge base, notifications, analytics.
-- Everything lives in its own "crm" schema so it never collides with tables the owner already has.
-- Idempotent: every statement is safe to run again.

create extension if not exists vector;
create schema if not exists crm;

-- ============================================================================================
-- Customers (one row per conversation partner). psid is prefixed by channel: "wa_<phone>",
-- "ig_<igsid>", or the plain Facebook PSID.
-- ============================================================================================
create table if not exists crm.customers (
  psid                      text primary key,
  platform                  text not null default 'facebook' check (platform in ('facebook', 'instagram', 'whatsapp')),
  name                      text,
  custom_name               text,
  username                  text,
  profile_pic_url           text,
  bot_active                boolean not null default true,
  comment_bot_active        boolean not null default true,
  handoff_requested         boolean not null default false,
  handoff_at                timestamptz,
  rolling_summary           text,
  last_customer_message_at  timestamptz,
  last_message_text         text,
  last_activity_at          timestamptz not null default now(),
  history_cleared_at        timestamptz,
  unread_count              integer not null default 0 check (unread_count >= 0),
  created_at                timestamptz not null default now()
);
create index if not exists customers_platform_activity_idx on crm.customers (platform, last_activity_at desc);
create index if not exists customers_unread_idx on crm.customers (unread_count) where unread_count > 0;

-- ============================================================================================
-- Messages. (platform, mid) is unique so a webhook delivered twice can never create a duplicate.
-- ============================================================================================
create table if not exists crm.messages (
  id               bigint generated always as identity primary key,
  psid             text not null references crm.customers(psid) on delete cascade,
  platform         text not null default 'facebook' check (platform in ('facebook', 'instagram', 'whatsapp')),
  sender           text not null default 'customer' check (sender in ('customer', 'bot', 'agent')),
  text             text not null default '',
  comment_id       text,
  mid              text,
  media_url        text,
  media_type       text,
  media_mime_type  text,
  media_filename   text,
  media_status     text,
  created_at       timestamptz not null default now()
);
create index if not exists messages_psid_created_idx on crm.messages (psid, created_at);
create index if not exists messages_comment_idx on crm.messages (comment_id) where comment_id is not null;
create unique index if not exists messages_platform_mid_key on crm.messages (platform, mid) where mid is not null;

-- ============================================================================================
-- Comments (Facebook and Instagram)
-- ============================================================================================
create table if not exists crm.comments (
  platform        text not null check (platform in ('facebook', 'instagram')),
  comment_id      text not null,
  post_id         text,
  parent_id       text,
  from_id         text,
  from_name       text,
  from_username   text,
  message         text,
  classification  text,
  topic           text,
  action          text,
  bot_reply       text,
  bot_active      boolean not null default true,
  hidden          boolean not null default false,
  is_read         boolean not null default false,
  created_at      timestamptz not null default now(),
  primary key (platform, comment_id)
);
create index if not exists comments_platform_created_idx on crm.comments (platform, created_at desc);
create index if not exists comments_unread_idx on crm.comments (platform) where is_read = false;

-- ============================================================================================
-- Tombstones and audit. Deleted items are remembered so a late webhook cannot resurrect them.
-- ============================================================================================
create table if not exists crm.deleted_comment_ids (
  platform    text not null,
  comment_id  text not null,
  deleted_at  timestamptz not null default now(),
  primary key (platform, comment_id)
);
create table if not exists crm.deleted_message_mids (
  platform    text not null,
  mid         text not null,
  deleted_at  timestamptz not null default now(),
  primary key (platform, mid)
);
create table if not exists crm.deletion_audit (
  id            bigint generated always as identity primary key,
  actor_id      text not null,
  action_type   text not null,
  platform      text not null,
  record_count  integer not null default 0,
  created_at    timestamptz not null default now()
);
create index if not exists deletion_audit_created_idx on crm.deletion_audit (created_at desc);

create table if not exists crm.activity_logs (
  id          bigint generated always as identity primary key,
  level       text not null default 'info',
  event       text not null,
  detail      jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists activity_logs_created_idx on crm.activity_logs (created_at desc);

-- ============================================================================================
-- Notifications
-- ============================================================================================
create table if not exists crm.notifications (
  id                 bigint generated always as identity primary key,
  type               text not null,
  platform           text not null,
  source_type        text not null,
  customer_id        text,
  customer_name      text,
  customer_username  text,
  target_id          text not null,
  message_preview    text,
  is_read            boolean not null default false,
  metadata           jsonb not null default '{}'::jsonb,
  created_at         timestamptz not null default now()
);
create index if not exists notifications_created_idx on crm.notifications (created_at desc);
create index if not exists notifications_unread_idx on crm.notifications (is_read) where is_read = false;

-- ============================================================================================
-- Knowledge base. A document's chunks are replaced inside ONE transaction, so readers always see
-- either the complete old set or the complete new set (never an empty window). Chunks keep a content
-- hash so unchanged text is never embedded twice.
-- ============================================================================================
create table if not exists crm.kb_documents (
  id            bigint generated always as identity primary key,
  source        text not null unique,
  title         text not null default '',
  content_hash  text not null,
  size_bytes    integer not null default 0,
  chunk_count   integer not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create table if not exists crm.kb_chunks (
  id            bigint generated always as identity primary key,
  document_id   bigint not null references crm.kb_documents(id) on delete cascade,
  position      integer not null,
  content       text not null,
  content_hash  text not null,
  embedding     vector(384) not null
);
create index if not exists kb_chunks_document_idx on crm.kb_chunks (document_id, position);
create index if not exists kb_chunks_hash_idx on crm.kb_chunks (content_hash);
create index if not exists kb_chunks_embedding_idx on crm.kb_chunks using hnsw (embedding vector_cosine_ops);

-- ============================================================================================
-- Analytics events (response time, handoffs, volume)
-- ============================================================================================
create table if not exists crm.metrics_events (
  id          bigint generated always as identity primary key,
  kind        text not null,
  platform    text not null,
  value       double precision,
  created_at  timestamptz not null default now()
);
create index if not exists metrics_kind_created_idx on crm.metrics_events (kind, created_at desc);

-- ============================================================================================
-- Functions and triggers
-- ============================================================================================
create or replace function crm.prevent_deleted_comment_reinsert()
returns trigger language plpgsql set search_path = crm, pg_temp as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(new.platform || ':' || new.comment_id, 0));
  if exists (select 1 from crm.deleted_comment_ids d
             where d.platform = new.platform and d.comment_id = new.comment_id) then
    return null;
  end if;
  return new;
end $$;

drop trigger if exists comments_prevent_deleted_reinsert on crm.comments;
create trigger comments_prevent_deleted_reinsert
  before insert on crm.comments
  for each row execute function crm.prevent_deleted_comment_reinsert();

create or replace function crm.prevent_stale_message_reinsert()
returns trigger language plpgsql set search_path = crm, pg_temp as $$
declare cleared timestamptz;
begin
  if new.mid is not null then
    perform pg_advisory_xact_lock(hashtextextended('message:' || new.platform || ':' || new.mid, 0));
    if exists (select 1 from crm.deleted_message_mids d where d.platform = new.platform and d.mid = new.mid) then
      return null;
    end if;
  end if;
  select c.history_cleared_at into cleared from crm.customers c where c.psid = new.psid for update;
  if cleared is not null and new.created_at <= cleared then
    return null;
  end if;
  return new;
end $$;

drop trigger if exists messages_prevent_stale_reinsert on crm.messages;
create trigger messages_prevent_stale_reinsert
  before insert on crm.messages
  for each row execute function crm.prevent_stale_message_reinsert();

-- Keeps the conversation list (preview, activity time, unread counter) in step with new messages.
create or replace function crm.after_message_insert()
returns trigger language plpgsql set search_path = crm, pg_temp as $$
begin
  update crm.customers c set
    last_message_text = left(coalesce(nullif(new.text, ''), case when new.media_type is not null then '[' || new.media_type || ']' else '' end), 200),
    last_activity_at = greatest(c.last_activity_at, new.created_at),
    last_customer_message_at = case when new.sender = 'customer'
        then greatest(coalesce(c.last_customer_message_at, new.created_at), new.created_at)
        else c.last_customer_message_at end,
    unread_count = case when new.sender = 'customer' then c.unread_count + 1 else c.unread_count end
  where c.psid = new.psid;
  return null;
end $$;

drop trigger if exists messages_after_insert on crm.messages;
create trigger messages_after_insert
  after insert on crm.messages
  for each row execute function crm.after_message_insert();

create or replace function crm.mark_conversation_read(p_psid text)
returns void language sql set search_path = crm, pg_temp as $$
  update crm.customers set unread_count = 0 where psid = p_psid and unread_count <> 0;
$$;

-- Call the platform API FIRST; call this only after it succeeded. Returns rows removed (0 = already gone).
create or replace function crm.delete_comment_records(
  p_comment_id text, p_platform text, p_actor_id text, p_action_type text default 'comment_deleted')
returns integer language plpgsql set search_path = crm, pg_temp as $$
declare n_messages integer := 0; n_comments integer := 0; fresh boolean;
begin
  if p_platform not in ('facebook', 'instagram') then
    raise exception 'Invalid platform';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_platform || ':' || p_comment_id, 0));
  insert into crm.deleted_comment_ids (platform, comment_id) values (p_platform, p_comment_id)
    on conflict do nothing;
  get diagnostics n_comments = row_count;
  fresh := n_comments > 0;
  delete from crm.messages where comment_id = p_comment_id and platform = p_platform;
  get diagnostics n_messages = row_count;
  delete from crm.comments where platform = p_platform and comment_id = p_comment_id;
  get diagnostics n_comments = row_count;
  if fresh or n_messages + n_comments > 0 then
    insert into crm.deletion_audit (actor_id, action_type, platform, record_count)
    values (coalesce(nullif(p_actor_id, ''), 'system'), p_action_type, p_platform, n_messages + n_comments);
  end if;
  return n_messages + n_comments;
end $$;

create or replace function crm.clear_conversation_history(p_psid text, p_actor_id text)
returns jsonb language plpgsql set search_path = crm, pg_temp as $$
declare plat text; stamp timestamptz; n integer := 0;
begin
  select platform into plat from crm.customers where psid = p_psid for update;
  if not found then raise exception 'Conversation not found'; end if;
  stamp := clock_timestamp();
  delete from crm.messages where psid = p_psid;
  get diagnostics n = row_count;
  update crm.customers set history_cleared_at = stamp, last_message_text = null,
         rolling_summary = null, unread_count = 0 where psid = p_psid;
  insert into crm.deletion_audit (actor_id, action_type, platform, record_count)
  values (coalesce(nullif(p_actor_id, ''), 'system'), 'chat_history_cleared', plat, n);
  return jsonb_build_object('deleted_count', n, 'history_cleared_at', stamp);
end $$;

create or replace function crm.delete_single_message(p_message_id bigint, p_actor_id text)
returns integer language plpgsql set search_path = crm, pg_temp as $$
declare m crm.messages%rowtype;
begin
  select * into m from crm.messages where id = p_message_id;
  if not found then return 0; end if;
  if m.mid is not null then
    perform pg_advisory_xact_lock(hashtextextended('message:' || m.platform || ':' || m.mid, 0));
    insert into crm.deleted_message_mids (platform, mid) values (m.platform, m.mid) on conflict do nothing;
  end if;
  delete from crm.messages where id = p_message_id;
  insert into crm.deletion_audit (actor_id, action_type, platform, record_count)
  values (coalesce(nullif(p_actor_id, ''), 'system'), 'single_message_deleted', m.platform, 1);
  return 1;
end $$;

create or replace function crm.match_chunks(
  query_embedding vector(384), match_count integer default 4, min_similarity double precision default 0.0)
returns table (id bigint, document_id bigint, content text, similarity double precision)
language sql stable set search_path = crm, public, extensions, pg_temp as $$
  select c.id, c.document_id, c.content, (1 - (c.embedding <=> query_embedding))::double precision as similarity
  from crm.kb_chunks c
  where (1 - (c.embedding <=> query_embedding)) >= min_similarity
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- ============================================================================================
-- Lock the schema down. The CRM connects with the database owner role. Supabase's public API roles
-- (anon, authenticated) must never be able to read this data, so RLS is on with no policies and
-- their privileges are revoked.
-- ============================================================================================
alter table crm.customers           enable row level security;
alter table crm.messages            enable row level security;
alter table crm.comments            enable row level security;
alter table crm.deleted_comment_ids enable row level security;
alter table crm.deleted_message_mids enable row level security;
alter table crm.deletion_audit      enable row level security;
alter table crm.activity_logs       enable row level security;
alter table crm.notifications       enable row level security;
alter table crm.kb_documents        enable row level security;
alter table crm.kb_chunks           enable row level security;
alter table crm.metrics_events      enable row level security;

do $$
declare r text;
begin
  revoke all on schema crm from public;
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on schema crm from %I', r);
      execute format('revoke all on all tables in schema crm from %I', r);
      execute format('revoke all on all functions in schema crm from %I', r);
    end if;
  end loop;
end $$;
