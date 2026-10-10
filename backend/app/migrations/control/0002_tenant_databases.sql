-- Control-plane migration 0002: each workspace's bring-your-own database connection.
-- The connection URL contains a password, so only its AES-256-GCM ciphertext is stored.

create table tenant_databases (
  tenant_id         uuid primary key references tenants(id) on delete cascade,
  encrypted_url     text not null,
  host_hint         text not null,                      -- "host:port/dbname", never the user or password
  status            text not null default 'connected'
                    check (status in ('connected', 'migrated', 'error')),
  last_checked_at   timestamptz,
  last_error        text check (length(last_error) <= 500),
  schema_version    text,                               -- latest applied tenant migration version
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
