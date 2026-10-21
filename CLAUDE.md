# Synthra CRM — CLAUDE.md

Multi-tenant social-inbox CRM: connects WhatsApp, Facebook (Messenger + comments) and Instagram (DMs + comments),
queues Meta webhooks, and answers with an LLM bot backed by a per-workspace knowledge base. Two apps: `backend/` (FastAPI)
and `frontend/` (Next.js dashboard, served at CRM_PUBLIC_URL, default http://localhost:3000; talks to the API directly).

## Stack (backend/requirements.txt)
Python 3.11–3.13 (pycache from both) · FastAPI 0.115.6 · uvicorn[standard] 0.34.0 · asyncpg 0.30.0 (raw SQL, no ORM)
· pydantic 2.10.4 + pydantic-settings 2.7.0 · argon2-cffi 23.1.0 · cryptography 44.0.0 · PyJWT 2.10.1 · httpx 0.28.1
· numpy 2.1.3 · pgvector 0.3.6 · fastembed 0.9.0 · pypdf 6.20.0 · python-multipart 0.0.32
Dev: pytest 8.3.4, pytest-asyncio 0.25.0, pgserver 0.1.4. DB: PostgreSQL + pgvector.

## Layout (synthra-crm/backend/app)
- main.py        app factory, lifespan wiring (all services on app.state), CORS, router registration, /healthz /readyz
- config.py      Settings (env prefix CRM_*), key/secret validators, production rules
- api/           routers: auth, database, channels, inbox, comments, kb, media, webhooks; common.py (workspace_pool,
                 throttle, audit, platform_client, meta_errors); deps.py (current_claims = Bearer JWT auth)
- auth/          AuthService (register/login/refresh rotation/reset), PgAuthRepository (repo_pg), memory_repo (tests)
- security/      crypto.py (AES-256-GCM SecretBox, key rotation), passwords.py (argon2id), tokens.py (JWT AccessClaims)
- channels/      registry.py (5 channels + field specs, single source of truth), service.py (encrypted config CRUD/test),
                 events.py (parse Meta payloads), signature.py (HMAC verify), probe.py
- jobs/          queue.py (durable PG queue, SKIP LOCKED, backoff, MAX_ATTEMPTS=6), worker.py
- bot/           engine.py (per-channel handlers: inbox replies, handoff, comment classify/reply/moderate), prompts, parsing, text
- llm/           client.py (anthropic/openai/gemini/groq over HTTPS, tenant brings own key), budget.py (daily call cap)
- kb/            service.py (chunk 800/120, hash-dedup, atomic swap), embedder.py (fastembed | hash), extract.py (pdf/text)
- platforms/     meta.py (Graph API client), factory.py
- tenantdb/      service.py (encrypted BYO DB URL, pool cache MAX_POOLS=50), provisioner.py (connect/migrate/manual_sql)
- crm/           store.py (all tenant-DB reads/writes), lookups.py
- migrations/    control/0001–0004 (shared DB), tenant/0001–0002 (each customer's DB, schema `crm`, idempotent)
- web.py (CSRF guard, refresh cookie, request-id/security-headers middleware) · errors.py · db.py · migrator.py · mailer.py · logging_setup.py
- tests/ (conftest, auth_service, channels_pg, channels_unit, tenantdb) · scripts/dev_postgres.py

## How it connects
Meta webhook -> /webhooks/{tenant}/{platform} (size limit, HMAC check per channel app secret, 403 on any failure; fans out to the platform's inbox + comments channels) -> events.extract ->
EventQueue.enqueue (control DB, before 200) -> Worker claims -> BotEngine handler -> tenant pool (workspace DB) ->
store.insert_message / customers -> KB retrieve (pgvector) + LLM -> MetaClient send. Dashboard routes:
current_claims -> workspace_pool(claims.tenant_id) -> crm.store / MetaClient.
Two databases: CONTROL (CRM_DATABASE_URL) and per-workspace TENANT DB (user-supplied Postgres/Supabase, URL stored encrypted).

## Schema
Control: users, tenants, tenant_members(role owner|admin|member), sessions, refresh_tokens, auth_tokens(verify_email|reset_password),
rate_limits, audit_log, tenant_databases(encrypted_url,status,schema_version), channel_configs(PK tenant+channel; encrypted_secrets,
settings jsonb, status), webhook_events(queue; unique tenant+channel+event_key), llm_usage(tenant,day,calls).
Tenant (schema crm): customers(psid PK: "wa_<phone>", "ig_<igsid>", or plain FB PSID), messages(unique platform+mid;
sender customer|bot|agent; media_*), comments(PK platform+comment_id), deleted_comment_ids, deleted_message_mids, deletion_audit,
activity_logs, notifications, kb_documents, kb_chunks(embedding vector(384), HNSW cosine), metrics_events, media(bytea).
Triggers block re-inserting deleted comments/messages and keep conversation preview/unread in sync; SQL funcs:
delete_comment_records, clear_conversation_history, delete_single_message, mark_conversation_read, match_chunks. RLS on, anon/authenticated revoked.
Channel ids: whatsapp, facebook_inbox, facebook_comments, instagram_inbox, instagram_comments.

## API routes
- /api/auth: POST register, verify-email, resend-verification, login, refresh(cookie+CSRF), logout, logout-all, forgot-password, reset-password; GET me
- /api/database: GET ""; POST test, migrate{dry_run}, check; PUT ""; GET migrations, setup.sql; DELETE ""
- /api/channels: GET ""; GET|PUT|DELETE /{id}; POST /{id}/test, /{id}/verify-token/rotate; GET /-/queue
- /api/inbox/{whatsapp|facebook|instagram}: GET conversations; GET conversations/{psid}/messages; POST .../read|send|send-image|bot|handoff/resolve;
  PATCH conversations/{psid}; DELETE conversations/{psid}/history; DELETE messages/{id}
- /api/comments/{facebook|instagram}: GET; POST read, bulk-delete, /{cid}/reply{public|private}, /{cid}/hide, /{cid}/bot; DELETE /{cid}
- /api/kb: GET; POST upload, text, search; DELETE /{id}
- /api/media/{id} (auth) · /public/media/{tenant}/{media}?exp&sig (HMAC, 10 min, JPEG/PNG only)
- /api/analytics?days · /api/notifications (GET ?unread&before_id&limit, /unread-count, POST /read{ids?}) · /api/activity
- /api/events  SSE live updates: event "marks" (counters fingerprint, no message text), ends after 540 s -> client reconnects with fresh token; max 5 per tenant
- /webhooks/{tenant_id}/{whatsapp|facebook|instagram} GET (verify handshake) / POST (legacy: last segment may still be a channel id) · /healthz · /readyz · /docs (non-prod only)

## Progress (branch complete-synthra)
Stage 1 backend unblock: DONE (backend confirmed running by the owner). Stage 2 backend tests: IN PROGRESS (written; still TODO: DB-backed tests for
inbox/comments/webhook endpoint/queue, needs CRM_TEST_DATABASE_URL).
Stage 3 frontend foundation (frontend/): DONE, unrun (run `npm install` + `npm run typecheck` + `npm run dev`): auth pages, API client, app shell, overview.
Stage 4 setup flows: WRITTEN, unrun (same commands). /settings/database (connect+test, dry-run preview, apply, migration table, manual SQL, disconnect),
/settings/channels (overview) and /settings/channels/[id] (credentials, webhook URL+verify token, bot behaviour, test, remove). Nav entries enabled.
Stage 5 dashboards: WRITTEN, unrun. Inbox (3 platforms): /inbox/[platform] (list+search+filters, chat, send text/image, bot toggle,
handoff resolve, rename, clear history, delete message, live refresh). Comments (FB, IG): /comments/[platform] (filters, public/private reply,
hide, bot toggle, delete platform-first, bulk delete with per-item failures). Knowledge base: /knowledge-base (upload file or paste text, document list
with inline delete confirm, retrieval test). Analytics: /analytics (7/14/30/90 d, KPIs, stacked daily bars in plain CSS, by-channel table, comment tone/action).
Notifications: /notifications (unread filter, mark one/all read, keyset "load more", links to inbox/comments). Nav has no `soon` entries left.
Stage 6 docs/deploy/OWASP: WRITTEN (not yet committed). README.md (Mermaid architecture, no-Docker quick start); docs/self-hosting.md, environment.md, security-review.md,
production-checklist.md (incl. owner to-do list), docs/channels/{whatsapp,facebook-inbox,facebook-comments,instagram-inbox,instagram-comments}.md (filenames = registry guide paths).
Meta steps were checked against Meta docs via search (IG-login permissions, WhatsApp system-user token, 24 h window) but NOT walked through in a live Meta app: verify once.
Security review is evidence-labelled (Verified / Per notes / Not reviewed); many files not read, dependency audits not run.
Deployment is NO-DOCKER by owner decision: deploy/ has systemd units (synthra-api, synthra-web) + Caddyfile (HSTS for app host, 20 MB body cap); control DB is any
plain PostgreSQL via CRM_DATABASE_URL. docker-compose.yml, both Dockerfiles and both .dockerignore are STILL ON DISK: delete with `git rm` (no shell tool worked); never add Docker/compose back.
Frontend no longer uses `output: "standalone"`: run `npm run build` then `npm run start` (NEXT_PUBLIC_API_URL must be set at build time, e.g. frontend/.env.production).
FASTEMBED_CACHE_PATH is set in the systemd unit (not a CRM_* setting, .env cannot carry it).

## Conventions
- `from __future__ import annotations`; one `router = APIRouter(prefix="/api/<x>")` per file; services live on app.state.
- Pydantic input models use `extra="forbid"`. Errors: raise AppError subclasses (BadRequest/NotFound/...) -> {"error":{code,message,request_id}}.
- EVERY data query must filter by claims.tenant_id (use workspace_pool). Mutating routes call throttle() and audit() (audit field NAMES, never values).
- Never log payloads, tokens or provider/Meta error text; Meta errors go through meta_errors()/MetaError.describe().
- Secrets: AES-256-GCM via SecretBox with AAD context "<tenant>:<channel>:secrets" / "<tenant>:database:url".
- Auth: 10-min Bearer access JWT + rotating refresh cookie `crm_refresh` (path /api/auth, SameSite=strict); cookie routes need header `X-CSRF-Protection: 1`.
- Platform delete flow: call platform API first, delete DB record only after success. Bot/queue handlers must be idempotent.
- Migrations: new numbered .sql in migrations/control or migrations/tenant; tenant ones must be idempotent (`if not exists`).
- Adding a channel field: edit channels/registry.py only.
- Frontend: TypeScript strict, Next.js App Router, Tailwind 4 with CSS-variable tokens in globals.css (auto light/dark). Shared primitives in
  components/ui.tsx. ALL API calls go through lib/api.ts (`api()`; access token in memory only, single-flight refresh, never localStorage).
  Route groups: (auth) public pages, (app) guarded by useRequireAuth. Sidebar defined once in lib/nav.ts.

## Setup (PowerShell, from synthra-crm/backend)
python -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env     # fill CRM_DATABASE_URL, CRM_MASTER_KEY, CRM_JWT_SECRET (>=32 chars)
# key: python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
uvicorn app.main:app --reload          # CRM_AUTO_MIGRATE=true applies control migrations on start
Offline/dev embeddings: CRM_EMBEDDER=hash (fastembed downloads ~90 MB on first use)
Tests: python -m pytest -q   (integration tests skipped unless CRM_TEST_DATABASE_URL set;
       `python scripts/dev_postgres.py` needs Python 3.9–3.12, pgserver has no 3.13 wheels)

## Frontend (synthra-crm/frontend; Next 16 + React 19 + TS + Tailwind 4)
- src/lib/: api.ts (client, authApi, ApiError), settings-api.ts (databaseApi, channelsApi, types, errorText, option lists), inbox-api.ts (inboxApi, fetchMediaBlob, isSetupError), comments-api.ts (commentsApi), kb-api.ts (kbApi, describeIndexResult, formatBytes), insights-api.ts (insightsApi: analytics/notifications, formatSeconds, timeAgo), live.ts (useLiveMarks: SSE via fetch, auto-reconnect), channel-status.ts, auth-context.tsx (AuthProvider/useAuth), guards.ts (useRequireAuth/useRedirectIfAuthenticated), nav.ts
- src/components/: ui.tsx (Button, Input, Alert, Card, Spinner, Logo), form.tsx (PageHeader, SectionCard, Badge, Textarea, Select, Toggle, CopyField, Skeleton), inbox.tsx (InboxView), comments.tsx (CommentsView), knowledge-base.tsx, analytics.tsx, notifications.tsx, app-shell.tsx (sidebar/drawer/user menu), icons.tsx
- src/app/: (auth)/{login,register,verify-email,forgot-password,reset-password} · (app)/dashboard · (app)/settings/{database,channels,channels/[id]} · (app)/inbox/[platform] · (app)/comments/[platform] · (app)/{knowledge-base,analytics,notifications} · error.tsx · not-found.tsx
  (page.tsx files must export only the default component; shared helpers go in lib/.)
- Optional env NEXT_PUBLIC_DOCS_URL: base URL of the published docs folder; channel pages link their guide there once set.
- Env: NEXT_PUBLIC_API_URL (API origin as the browser sees it; app+API must be same-site for the SameSite=strict cookie).
  Backend CRM_PUBLIC_URL must equal the origin serving the app (CSRF origin check + email links).
- Run (PowerShell, from synthra-crm/frontend): copy .env.example .env.local ; npm install ; npm run dev   (typecheck: npm run typecheck)

## Known issues / TODOs
1. (Stage 1, done) api/insights.py added: analytics, notifications, activity, SSE stream; .env.example completed; tests/test_app_boot.py guards route registration.
4. Tests cover only auth, channels, tenantdb. No tests for bot engine, kb, inbox, comments, webhooks, media. No frontend tests yet.
5. Docs now exist (Stage 6). Frontend (Stages 3-5) was written without a shell: run npm install / typecheck / build once. How the frontend joins NEXT_PUBLIC_DOCS_URL with the backend guide path /docs/channels/<id>.md is not verified.
6. Git warns LF->CRLF on Windows; consider a .gitattributes.
9. notifications.tsx routes by `source_type` containing "comment" (-> /comments/<platform>) else /inbox/<platform>; the engine's actual type/source_type values were not verified.
7. Sibling folder ../fbbot (with whatsapp-dashboard, Next 16 JS, single-tenant, direct Supabase) is the earlier standalone bot; reuse its UI ideas
   (ConversationList, MediaMessage, NotificationCenter, hooks) for Stage 5 but rewire to this API.
8. Unverified (skimmed only): channels/service.py, bot/prompts.py, bot/parsing.py, crm/lookups.py,
   kb/embedder.py, auth/windows.py, platforms/factory.py. Re-read before editing them. (channels/events.py and api/webhooks.py were read in Stage 6: OK.)
10. (FIXED) Meta allows ONE callback URL per object per app, so webhooks are now per PLATFORM: /webhooks/{tenant}/{whatsapp|facebook|instagram} (3 total). A request is verified once (verify token of any channel on the platform; X-Hub-Signature-256 per channel app secret) and fanned out: each configured+enabled channel of the platform parses the payload for its own kind and enqueues under its own channel id, so the worker/engine are unchanged. Channels on one platform share one verify token (rotation applies to all). Legacy per-channel URLs (/webhooks/{tenant}/facebook_comments ...) still work and serve one channel. See api/webhooks.py, channels/registry.py (PLATFORMS, resolve_webhook_target), tests/test_webhooks_unified.py.
    (Previously each channel had its own URL; superseded by the platform-level webhooks above.)
    Implemented: /webhooks/{tenant}/{platform} verifies once and dispatches to both channels.
11. No key-rotation script: SecretBox supports CRM_MASTER_KEY_PREVIOUS/needs_rotation() but nothing re-encrypts tenant_databases.encrypted_url / channel_configs.encrypted_secrets.
12. Instagram tokens expire (~60 d); no verified refresh or expiry warning.
13. Webhook per-workspace rate limit runs after the signature check (bad-signature floods are not limited in-app; add proxy limits). TenantDatabaseService.pool() uses one global lock.
14. Security review (docs/security-review.md) is partial: auth/service, crm/store, api/{inbox,comments,kb,media,insights}, bot/*, llm/*, kb/*, platforms/*, provisioner.py not read; pip-audit/npm audit not run.
15. Docker leftovers still on disk (see Stage 6 note). Dev shell tool timed out in the Stage 6 session; no commands were run (no git, tests, typecheck).
