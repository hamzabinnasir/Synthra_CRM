# Security review (OWASP Top 10, 2021)

## Scope and how to read this

This is a code-assisted review, not a penetration test and not a guarantee. Each item carries one of three evidence labels:

- **Verified**: I read the code that implements it (files named in the row).
- **Per notes**: stated in `CLAUDE.md` or the docs but **not re-checked in code** during this review.
- **Not reviewed**: outside what was read. These need a look before you rely on them.

Files read for this review: `app/main.py`, `app/config.py`, `app/web.py`, `app/api/webhooks.py`, `app/channels/registry.py`, `app/channels/events.py`, `app/security/crypto.py`, `app/tenantdb/service.py`, `frontend/next.config.mjs`, `deploy/*`.

Not read: `auth/service.py`, `auth/repo_pg.py`, `security/passwords.py`, `security/tokens.py`, `crm/store.py`, `api/inbox.py`, `api/comments.py`, `api/kb.py`, `api/media.py`, `api/insights.py`, `bot/*`, `llm/*`, `kb/*`, `platforms/*`, `tenantdb/provisioner.py`, the SQL migrations, and the frontend source apart from `next.config.mjs`.

Dependency scanners (`pip-audit`, `npm audit`) were **not run**.

## Findings that need action

| # | Severity | Finding | Recommendation |
| --- | --- | --- | --- |
| 1 | ~~High (design)~~ **Resolved** | Meta allows one callback URL per object per app, and Facebook inbox and comments share the `page` object while Instagram inbox and comments share `instagram`. Each channel used to have its own URL and parser, so with one Meta app half the events were silently dropped. | Fixed. Webhooks are now per platform (`/webhooks/{tenant}/{whatsapp|facebook|instagram}`): the signature is verified once per channel app secret and events are fanned out to the inbox and comments channels (`api/webhooks.py`, `channels/registry.py`). Old per-channel URLs still work and serve one channel. Covered by `tests/test_webhooks_unified.py`. |
| 2 | Medium | Master key rotation is designed (`SecretBox`, `CRM_MASTER_KEY_PREVIOUS`, `needs_rotation()`) but there is **no script that re-encrypts stored values** (`backend/scripts/` contains only `dev_postgres.py`). The old key must stay configured indefinitely. | Add `scripts/rotate_keys.py` covering `tenant_databases.encrypted_url` and `channel_configs.encrypted_secrets`, with a dry run. I did not write it blind because the exact stored format of `encrypted_secrets` was not verified. |
| 3 | Medium | Instagram access tokens last about 60 days. I found no verified refresh flow. An expired token makes the channel fail until the user pastes a new one. | Surface token expiry/failed status on the channel page and notify the owner, or implement refresh. |
| 4 | Low | In `api/webhooks.py` the per-workspace rate limit runs **after** the signature check. Unauthenticated bad-signature requests still cost a database lookup and an HMAC over up to 1 MB each. Workspace IDs are random UUIDs, which limits abuse. | Add a per-IP limit at the proxy (Caddy rate-limit module, nginx `limit_req`, or a firewall rule) on `/webhooks/`. The Caddyfile already caps body size. |
| 5 | Low | `TenantDatabaseService.pool()` holds one process-wide lock while it looks up the workspace, resolves DNS and creates the pool. A slow workspace can briefly delay others. | Use a lock per workspace id. |
| 6 | Low | `RequestContextMiddleware` is a `BaseHTTPMiddleware`. These can interact badly with long-lived streaming responses (the `/api/events` SSE stream). | Load-test SSE with many clients; convert to a pure ASGI middleware if connections leak. |
| 7 | Low | Frontend CSP allows `'unsafe-inline'` scripts (`next.config.mjs`), documented as required by Next.js bootstrap scripts. | Move to nonce-based CSP later. Not urgent while React escapes output and no raw HTML is rendered (verify with a search for `dangerouslySetInnerHTML` in `frontend/src`). |
| 8 | Medium (unverified) | Customer messages and comments are untrusted text sent to an LLM that drives replies and, for comments, **moderation actions** (hide, delete). A crafted comment can try to steer the model. | Review `bot/prompts.py` and `bot/parsing.py`: output must be parsed against a strict schema, the model must have no tool access, delete should require a high-confidence classification, and the system prompt and knowledge base must be treated as readable by customers. |
| 9 | Fixed | The dashboard host had no HSTS header (the API sets it in production; Next.js does not). | Added to `deploy/Caddyfile`. |

## OWASP Top 10 (2021)

### A01 Broken access control

| Control | Evidence |
| --- | --- |
| Webhook endpoints return the same bare 403 for unknown workspace, unknown channel, unconfigured channel, wrong verify token and bad signature, so workspaces cannot be enumerated. | **Verified** (`api/webhooks.py`) |
| Verify-handshake attempts are limited to 120 per hour per workspace. | **Verified** |
| Every control-database query filters by `claims.tenant_id`; dashboard data lives in each customer's own database reached through `workspace_pool`. | **Per notes** |
| Tenant roles (`owner`, `admin`, `member`) enforced on mutating routes. | **Not reviewed** |
| Public media links are HMAC-signed and expire after 10 minutes. | **Per notes** |
| Media and SSE endpoints (authentication, per-tenant limits). | **Not reviewed** |

### A02 Cryptographic failures

| Control | Evidence |
| --- | --- |
| AES-256-GCM with a random 12-byte nonce, key fingerprint for rotation, and per-field AAD context (`<tenant>:<channel>:secrets`, `<tenant>:database:url`) so ciphertext cannot be moved between tenants or fields. Decrypt errors are deliberately vague. | **Verified** (`security/crypto.py`, `tenantdb/service.py`) |
| Secrets are masked for display (last 4 characters at most). | **Verified** (`mask_secret`); that the API never returns raw secrets is **Per notes** |
| Config rejects a master key that is not base64 of 32 bytes and a JWT secret under 32 characters. | **Verified** (`config.py`) |
| Production requires an `https://` public URL and Secure cookies; HSTS is sent by the API (max-age 2 years). | **Verified** (`config.py`, `web.py`) |
| argon2id password hashing. | **Per notes** |
| Key rotation tooling. | **Gap**, finding 2 |

### A03 Injection

| Control | Evidence |
| --- | --- |
| All database access uses asyncpg with parameters (the code read uses `$1`-style placeholders; there is no ORM). | **Verified** in the files read; `crm/store.py` and the SQL functions **Not reviewed** |
| Channel credentials and bot settings are validated with allow-list regexes and enums, with length limits. | **Verified** (`channels/registry.py`) |
| Pydantic models forbid extra fields. | **Per notes** |
| Webhook payloads are parsed only after the signature is valid, and only expected fields are read. | **Verified** (`api/webhooks.py`, `channels/events.py`) |
| Prompt injection against the LLM. | **Gap**, finding 8 |

### A04 Insecure design

| Control | Evidence |
| --- | --- |
| Durable queue: the event is stored **before** the 200 response; unique `(tenant, channel, event_key)` makes redelivery idempotent; at most 200 events per request; 1 MB body limit. | **Verified** (`api/webhooks.py`, `channels/events.py`) |
| Platform-first delete (call the platform, remove the record only on success). | **Per notes** |
| Per-workspace LLM daily call cap and webhook rate limit. | **Verified** (`config.py`); enforcement in `llm/budget.py` **Not reviewed** |
| Meta one-callback-URL constraint. | **Gap**, finding 1 |

### A05 Security misconfiguration

| Control | Evidence |
| --- | --- |
| API sets `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, COOP, `Cache-Control: no-store` and a restrictive CSP on `/api/`, plus HSTS in production. | **Verified** (`web.py`) |
| Dashboard sets the same headers and a CSP restricted to self and the API origin in production builds. | **Verified** (`next.config.mjs`) |
| CORS allows only the configured dashboard origins, credentials enabled, explicit methods and headers. | **Verified** (`main.py`) |
| `/docs` and OpenAPI are disabled in production. | **Verified** (`main.py`) |
| Services bind to `127.0.0.1`; only Caddy is public; systemd units run unprivileged with `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`. | **Verified** (`deploy/`) |
| Tenants cannot point their database at private or loopback addresses unless `CRM_ALLOW_PRIVATE_DB_HOSTS=true`. Keep it `false` on shared hosting. | **Verified** (`tenantdb/service.py`) |
| `backend/.env` should be mode 600 and owned by the service user. | Documented in [self-hosting](self-hosting.md) |

### A06 Vulnerable and outdated components

| Control | Evidence |
| --- | --- |
| Versions are pinned in `requirements.txt` and `package-lock.json`. | **Per notes** |
| Vulnerability scan of dependencies. | **Not done.** Run `pip-audit -r backend/requirements.txt` and `npm audit` in `frontend/`, and schedule them (for example weekly or via Dependabot). |
| Meta Graph API versions are configuration, not code. | **Verified** (`CRM_GRAPH_VERSION`, `CRM_INSTAGRAM_GRAPH_VERSION`). Meta retires old versions; check their changelog at least twice a year. |

### A07 Identification and authentication failures

| Control | Evidence |
| --- | --- |
| Login lockout (5 failures per 15 minutes per account, 40 attempts per IP) and email verification, configurable. | **Verified** as settings (`config.py`); enforcement logic **Not reviewed** |
| 10-minute access JWT, rotating refresh token in an httpOnly, Secure, `SameSite=strict` cookie scoped to `/api/auth`; reuse grace window is 10 seconds. | **Verified** (`web.py`, `config.py`) |
| Refresh-token reuse detection and server-side session revocation on logout; token and password reset single use. | **Per notes**, **Not reviewed** |
| No tokens in `localStorage`. | **Per notes**; verify by searching `frontend/src` for `localStorage`/`sessionStorage` |
| Client IP is read from `X-Forwarded-For` only for the configured number of trusted proxy hops. | **Verified** (`web.py`). A wrong `CRM_TRUSTED_PROXY_HOPS` weakens every per-IP limit. |

### A08 Software and data integrity failures

| Control | Evidence |
| --- | --- |
| Meta webhooks are authenticated with HMAC-SHA256 (`X-Hub-Signature-256`) using the workspace's own app secret, over the raw body. | **Verified** (`api/webhooks.py`; algorithm in `channels/signature.py` **Not reviewed**) |
| Migrations are versioned and idempotent, with dry run. | **Per notes**, **Not reviewed** |
| CSRF on cookie-authenticated routes: custom header `X-CSRF-Protection: 1` **and** an `Origin` allow-list check. | **Verified** (`web.py`) |

### A09 Security logging and monitoring failures

| Control | Evidence |
| --- | --- |
| One access-log line per request with method, path, status and duration; no headers, bodies or tokens. Request IDs on every response. | **Verified** (`web.py`) |
| Rejected webhooks log channel and workspace ID only. | **Verified** (`api/webhooks.py`) |
| Audit log of mutating actions, recording field **names**, never values. | **Per notes**; table exists in the control schema |
| Alerting. | **Gap.** Nothing alerts on repeated 403s, queue backlog, failed logins or failing channels. Add external uptime monitoring and log-based alerts. |

### A10 Server-side request forgery

| Control | Evidence |
| --- | --- |
| Customer-supplied database hosts are resolved and checked against private ranges, then the connection uses the **resolved address** with the original hostname for TLS verification, which defeats DNS rebinding. | **Verified** (`tenantdb/service.py`, relies on `provisioner.resolve_public`, whose code was **Not reviewed**) |
| Outbound calls to Meta and LLM providers go to fixed provider hosts, with no user-controlled URLs. | **Per notes**, **Not reviewed** (`platforms/`, `llm/`) |

## Suggested next steps, in order

1. ~~Decide on finding 1 (platform-level webhook URL)~~ Done: three platform-level webhooks are implemented; users need one Meta app per platform, not per channel.
2. Run `pip-audit` and `npm audit`; fix anything high or critical.
3. Read the **Not reviewed** auth and store code with the checklist above, especially tenant filtering in `crm/store.py` and the media/SSE endpoints.
4. Review the bot prompts and output parsing for injection resistance (finding 8).
5. Add per-IP rate limiting for `/webhooks/` and `/api/auth/` at the proxy.
6. Write the key rotation script (finding 2) and test a rotation on a copy of the database.
7. Run an external scan (for example `testssl.sh` against both hostnames, and OWASP ZAP baseline against a staging copy).
