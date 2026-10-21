# Environment variable reference

The backend reads variables from the process environment and from `backend/.env`. Unknown keys in `.env` are ignored.
Source of truth: `backend/app/config.py`. Templates: `backend/.env.example` (development) and `.env.example` (production).

Generate any 32-byte key with:

```
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

## Required

| Variable | Description |
| --- | --- |
| `CRM_DATABASE_URL` | Control-plane PostgreSQL URL. Holds accounts, sessions, encrypted settings and the webhook queue. |
| `CRM_MASTER_KEY` | Base64 of exactly 32 bytes. Encrypts every stored credential (AES-256-GCM). **Back it up**: without it saved credentials cannot be recovered. |
| `CRM_JWT_SECRET` | At least 32 characters. Signs access tokens and public media links. |

## Core

| Variable | Default | Description |
| --- | --- | --- |
| `CRM_ENV` | `development` | `development` or `production`. Production requires an `https://` `CRM_PUBLIC_URL`, requires SMTP when email verification is on, hides `/docs`, enables HSTS and Secure cookies. |
| `CRM_AUTO_MIGRATE` | `false` | Apply control-plane migrations on startup. If false and migrations are pending, the API refuses to start. The example files set it to `true`. |
| `CRM_ALLOW_PRIVATE_DB_HOSTS` | `false` | Let workspaces connect to databases on private or loopback addresses. Keep `false` when hosting for other people (prevents probing your internal network). `true` is fine when only you use the server. |
| `CRM_MASTER_KEY_PREVIOUS` | unset | Old master key, set only while rotating keys. Values encrypted with it stay readable. |

## URLs, CORS and proxy

| Variable | Default | Description |
| --- | --- | --- |
| `CRM_PUBLIC_URL` | `http://localhost:3000` | The exact origin that serves the dashboard. Used for the CSRF origin check, CORS and email links. Must be `https://` in production. |
| `CRM_API_PUBLIC_URL` | `http://localhost:8000` | Public base URL of this API as Meta reaches it. Webhook URLs shown in the dashboard are built from it. |
| `CRM_ALLOWED_ORIGINS` | empty | Extra dashboard origins, comma-separated. |
| `CRM_TRUSTED_PROXY_HOPS` | `0` | Number of reverse proxies in front of the API (0-5). Used to read the client IP from `X-Forwarded-For`. Set `1` with Caddy. A wrong value breaks rate limiting. |

The dashboard and API must be on the same site (for example `app.example.com` and `api.example.com`), because the refresh cookie is `SameSite=strict`.

## Authentication and email

| Variable | Default | Description |
| --- | --- | --- |
| `CRM_REQUIRE_EMAIL_VERIFICATION` | `true` | Require a verified email before login. |
| `CRM_SMTP_HOST` | unset | SMTP server. Without it, development prints links to the server log. Required in production while verification is on. |
| `CRM_SMTP_PORT` | `587` | SMTP port. |
| `CRM_SMTP_USER` / `CRM_SMTP_PASSWORD` | unset | SMTP credentials. |
| `CRM_SMTP_FROM` | `Synthra CRM <no-reply@localhost>` | Sender shown on emails. Use an address on a domain you control. |
| `CRM_ACCESS_TTL_MINUTES` | `10` | Access token lifetime (1-60). |
| `CRM_REFRESH_TTL_DAYS` | `30` | Refresh token lifetime (1-90). |
| `CRM_REFRESH_REUSE_GRACE_SECONDS` | `10` | Window in which a just-rotated refresh token is tolerated (0-60), to survive double requests. |
| `CRM_VERIFY_TTL_HOURS` | `24` | Email verification link lifetime. |
| `CRM_RESET_TTL_MINUTES` | `60` | Password reset link lifetime (minimum 5). |
| `CRM_LOGIN_MAX_FAILURES` | `5` | Failed logins per account within the window before lockout. |
| `CRM_LOGIN_WINDOW_MINUTES` | `15` | Lockout window. |
| `CRM_LOGIN_IP_MAX_ATTEMPTS` | `40` | Login attempts per IP within the window. |

## Webhooks and worker

| Variable | Default | Description |
| --- | --- | --- |
| `CRM_WEBHOOK_MAX_BODY_BYTES` | `1000000` | Largest accepted webhook body (minimum 10000). Larger requests get 413. |
| `CRM_WEBHOOK_RATE_PER_MINUTE` | `1200` | Per-workspace webhook rate limit (minimum 10). |
| `CRM_RUN_WORKER` | `true` | Run the queue worker inside the API process. Set `false` on API-only replicas. |
| `CRM_WORKER_CONCURRENCY` | `8` | Events processed in parallel (1-64). |
| `CRM_GRAPH_VERSION` | `v21.0` | Facebook / WhatsApp Graph API version (format `vNN.N`). |
| `CRM_INSTAGRAM_GRAPH_VERSION` | `v25.0` | Instagram Graph API version. |

## Bot, LLM and knowledge base

| Variable | Default | Description |
| --- | --- | --- |
| `CRM_LLM_DAILY_CALLS` | `2000` | Per workspace, per UTC day. Protects the customer's own LLM key from loops and abuse. |
| `CRM_LLM_TIMEOUT_SECONDS` | `25` | Timeout for each LLM call (3-120). |
| `CRM_EMBEDDER` | `fastembed` | `fastembed` (real embeddings, downloads about 90 MB once) or `hash` (offline/dev only, much weaker retrieval). |
| `CRM_MAX_UPLOAD_BYTES` | `5000000` | Largest knowledge base upload. |
| `CRM_MAX_MEDIA_BYTES` | `16000000` | Largest media file stored. |
| `CRM_MAX_KB_DOCUMENTS` | `200` | Documents per workspace. |

## Not `CRM_*` variables

| Variable | Where | Description |
| --- | --- | --- |
| `FASTEMBED_CACHE_PATH` | Process environment (set in `deploy/synthra-api.service`) | Directory for the embedding model so it survives restarts. It is read by the fastembed library, not by the app, so putting it in `.env` has no effect. |
| `NEXT_PUBLIC_API_URL` | `frontend/.env.local` (dev) or `frontend/.env.production` (production) | API origin as the browser sees it. **Baked in at build time**: change it, then run `npm run build` again. |
| `NEXT_PUBLIC_DOCS_URL` | Frontend env | Optional base URL of the published docs folder. Channel pages link their setup guide there once set. |

## Where each secret comes from

| Secret | Obtain from | Put it in |
| --- | --- | --- |
| Control database URL | Your Postgres host or Supabase/Neon project | `CRM_DATABASE_URL` in `backend/.env` |
| Master key, JWT secret | Generate locally (command above) | `backend/.env` |
| SMTP credentials | Your email provider (Resend, SendGrid, Postmark, Amazon SES, your domain's mail host) | `CRM_SMTP_*` in `backend/.env` |
| Meta app secret, access tokens, IDs | Meta for Developers (see the channel guides) | The dashboard channel pages (stored encrypted), **not** `.env` |
| LLM API key | The customer's provider account | The dashboard channel pages (stored encrypted) |
| Workspace database URL | The customer's Supabase/Postgres | Dashboard > Settings > Database (stored encrypted) |
