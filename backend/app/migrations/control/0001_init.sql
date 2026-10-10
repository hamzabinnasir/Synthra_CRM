-- Control-plane schema, migration 0001: accounts, workspaces (tenants), sessions, one-time tokens,
-- shared rate limiting and the audit log.

create table users (
  id                 uuid primary key default gen_random_uuid(),
  email              text not null check (email = lower(email) and length(email) <= 254),
  email_verified_at  timestamptz,
  password_hash      text not null,
  display_name       text not null default '' check (length(display_name) <= 100),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create unique index users_email_key on users (email);

create table tenants (
  id             uuid primary key default gen_random_uuid(),
  name           text not null check (length(name) between 1 and 100),
  owner_user_id  uuid not null references users(id) on delete restrict,
  created_at     timestamptz not null default now()
);

create table tenant_members (
  tenant_id   uuid not null references tenants(id) on delete cascade,
  user_id     uuid not null references users(id) on delete cascade,
  role        text not null check (role in ('owner', 'admin', 'member')),
  created_at  timestamptz not null default now(),
  primary key (tenant_id, user_id)
);
create index tenant_members_user_idx on tenant_members (user_id);

create table sessions (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references users(id) on delete cascade,
  created_at      timestamptz not null default now(),
  last_used_at    timestamptz not null default now(),
  revoked_at      timestamptz,
  revoked_reason  text,
  ip              text check (length(ip) <= 64),
  user_agent      text check (length(user_agent) <= 400)
);
create index sessions_user_active_idx on sessions (user_id) where revoked_at is null;

-- Refresh tokens rotate on every use. A token presented twice means it was stolen or replayed.
create table refresh_tokens (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid not null references sessions(id) on delete cascade,
  token_hash  text not null unique,
  expires_at  timestamptz not null,
  used_at     timestamptz,
  created_at  timestamptz not null default now()
);
create index refresh_tokens_session_idx on refresh_tokens (session_id);
create index refresh_tokens_expires_idx on refresh_tokens (expires_at);

create table auth_tokens (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users(id) on delete cascade,
  purpose     text not null check (purpose in ('verify_email', 'reset_password')),
  token_hash  text not null unique,
  expires_at  timestamptz not null,
  used_at     timestamptz,
  created_at  timestamptz not null default now()
);
create index auth_tokens_user_purpose_idx on auth_tokens (user_id, purpose);

create table rate_limits (
  key           text not null,
  window_start  timestamptz not null,
  count         integer not null default 0,
  primary key (key, window_start)
);
create index rate_limits_window_idx on rate_limits (window_start);

create table audit_log (
  id          bigint generated always as identity primary key,
  tenant_id   uuid,
  user_id     uuid,
  event       text not null,
  ip          text,
  user_agent  text,
  detail      jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index audit_log_tenant_created_idx on audit_log (tenant_id, created_at desc);
create index audit_log_user_created_idx on audit_log (user_id, created_at desc);
