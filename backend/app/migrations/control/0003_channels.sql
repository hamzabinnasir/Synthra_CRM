-- Control-plane migration 0003: per-workspace channel configuration and the durable webhook queue.

-- One row per workspace and channel. Credentials live only inside `encrypted_secrets`
-- (one AES-256-GCM blob holding a JSON object, authenticated with "<tenant>:<channel>:secrets").
create table channel_configs (
  tenant_id         uuid not null references tenants(id) on delete cascade,
  channel           text not null
                    check (channel in ('whatsapp', 'facebook_inbox', 'facebook_comments',
                                       'instagram_inbox', 'instagram_comments')),
  enabled           boolean not null default false,     -- webhook events are accepted and queued
  bot_enabled       boolean not null default true,      -- the bot may reply on this channel
  encrypted_secrets text not null default '',
  secret_keys       text[] not null default '{}',       -- names only, so the UI can show "saved" without decrypting
  settings          jsonb not null default '{}'::jsonb, -- non-secret ids and bot behaviour
  status            text not null default 'not_configured'
                    check (status in ('not_configured', 'configured', 'connected', 'error')),
  last_tested_at    timestamptz,
  last_error        text check (length(last_error) <= 500),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  primary key (tenant_id, channel)
);

-- Durable queue. A row is inserted by the webhook handler *before* it answers Meta, so an accepted
-- event is never lost. The unique key makes redelivery by Meta harmless (idempotency).
create table webhook_events (
  id           bigserial primary key,
  tenant_id    uuid not null references tenants(id) on delete cascade,
  channel      text not null,
  event_key    text not null check (length(event_key) <= 200),
  payload      jsonb not null,
  status       text not null default 'pending'
               check (status in ('pending', 'processing', 'done', 'dead')),
  attempts     integer not null default 0,
  run_after    timestamptz not null default now(),
  locked_at    timestamptz,
  locked_by    text,
  last_error   text check (length(last_error) <= 500),
  received_at  timestamptz not null default now(),
  finished_at  timestamptz,
  unique (tenant_id, channel, event_key)
);

create index webhook_events_ready on webhook_events (run_after, id) where status = 'pending';
create index webhook_events_stale on webhook_events (locked_at) where status = 'processing';
create index webhook_events_finished on webhook_events (finished_at) where status in ('done', 'dead');
create index webhook_events_tenant on webhook_events (tenant_id, status);
