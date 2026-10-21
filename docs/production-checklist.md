# Production-readiness checklist and your to-do list

Tick these off before inviting real users. Items marked **(you)** can only be done by you, outside the code.

## A. Part 1: What you must do yourself

### Accounts and platforms to visit

| Platform | Why | What to obtain | Where it goes |
| --- | --- | --- | --- |
| **Domain registrar / DNS** | Public HTTPS addresses | Two `A` records, for example `app.` and `api.` on the **same domain** | `CRM_PUBLIC_URL`, `CRM_API_PUBLIC_URL`, `NEXT_PUBLIC_API_URL` |
| **Hosting** (a Linux VPS, or a Platform-as-a-Service that runs plain Python and Node) | Runs API + dashboard | Server with 2 GB RAM or more, SSH access | [Self-hosting guide](self-hosting.md) |
| **PostgreSQL** for the control database (local install, Supabase, Neon, RDS) | Accounts, sessions, encrypted settings, webhook queue | Connection URL | `CRM_DATABASE_URL` in `backend/.env` |
| **Email provider** (Resend, SendGrid, Postmark, Amazon SES, or your mail host) | Verification and reset emails; **required in production** | SMTP host, port, user, password; a verified sending domain (SPF, DKIM) | `CRM_SMTP_*` in `backend/.env` |
| **Meta for Developers** (developers.facebook.com) | WhatsApp, Facebook, Instagram access | Apps, app secrets, access tokens, IDs, App Review approval | Dashboard channel pages (not `.env`); see the five channel guides |
| **Meta Business Suite** (business.facebook.com) | Business verification, system user token for WhatsApp, WhatsApp number | Verified business, system user token | WhatsApp channel page |
| **LLM provider** (Anthropic, OpenAI, Google Gemini or Groq) | Bot answers | API key (customers bring their own) | Each channel page, stored encrypted |
| **Supabase** (or any PostgreSQL with pgvector) | **Each customer's** workspace database | Connection string with DDL rights (the direct or session-pooler Postgres URL, **not** the anon/service keys) | Dashboard > Settings > Database |
| **Password manager or secrets vault** **(you)** | Safe copy of `CRM_MASTER_KEY` and `.env` | Store both offline | n/a |
| **Uptime monitor** (UptimeRobot, Better Stack, ...) | Alert when the API is down | Monitor on `https://api.example.com/readyz` | n/a |
| **Legal** **(you)** | Meta requires a public privacy policy; you also need terms of service | Published URLs | Meta app settings; the repo has `Synthra-Privacy-Policy.docx` to publish as a web page |

Supabase anon and service keys **cannot create tables**. Synthra's automatic setup needs a direct PostgreSQL connection string (Supabase: Project settings > Database > Connection string). Explain this to your users; the database settings page does too.

### Variables to generate and set (backend/.env)

| Variable | How |
| --- | --- |
| `CRM_MASTER_KEY` | `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`. **Back it up.** |
| `CRM_JWT_SECRET` | Same command (any 32+ random characters). |
| `CRM_DATABASE_URL`, `CRM_PUBLIC_URL`, `CRM_API_PUBLIC_URL`, `CRM_TRUSTED_PROXY_HOPS=1`, `CRM_SMTP_*`, `CRM_ENV=production` | See [environment.md](environment.md). |

Frontend: `NEXT_PUBLIC_API_URL` in `frontend/.env.production`, **before** `npm run build`.

### Meta app review you will have to pass

| Channel | Permissions to request for public use |
| --- | --- |
| WhatsApp inbox | `whatsapp_business_messaging`, `whatsapp_business_management` (plus business verification and a real number) |
| Facebook inbox | `pages_messaging`, `pages_manage_metadata` |
| Facebook comments | `pages_read_engagement`, `pages_manage_engagement`, `pages_manage_posts` |
| Instagram inbox | `instagram_business_basic`, `instagram_business_manage_messages` |
| Instagram comments | `instagram_business_basic`, `instagram_business_manage_comments` |

Meta will ask for a privacy policy URL, a screencast of each feature and, for business use, Business verification. Allow days to weeks. Until approved, only people with roles on the app can use it, which is enough for testing.

## B. Part 2: Before launch

### Code and build

- [ ] Delete the leftover Docker files: `git rm docker-compose.yml backend/Dockerfile backend/.dockerignore frontend/Dockerfile frontend/.dockerignore`, then `git grep -n -i docker` returns nothing.
- [ ] `cd frontend && npm install && npm run typecheck && npm run build` pass. The frontend was written without a shell and has never been run.
- [ ] `cd backend && python -m pytest -q` passes with `CRM_TEST_DATABASE_URL` set (integration tests included).
- [ ] Add the missing tests: inbox, comments, webhook endpoint, queue, bot engine, knowledge base, media. Currently only auth, channels and tenant database are covered.
- [ ] Decide on and implement the Meta one-callback-URL fix, or document the two-apps requirement to users ([security review](security-review.md), finding 1).
- [ ] Verify the frontend notification routing against the real `type` / `source_type` values the bot engine writes (known issue 9 in `CLAUDE.md`).
- [ ] Confirm the channel guide links work: set `NEXT_PUBLIC_DOCS_URL` and open each channel page's guide link. The backend sends `/docs/channels/<id>.md`; verify how the frontend joins it to the base URL.
- [ ] Publish the `docs/` folder somewhere users can read it (the repository's web view or a static site).

### Security

- [ ] Work through [security-review.md](security-review.md): dependency audits, the **Not reviewed** code, prompt-injection review, per-IP proxy rate limits.
- [ ] `CRM_ENV=production`, `CRM_ALLOW_PRIVATE_DB_HOSTS=false` (if hosting for others), `CRM_TRUSTED_PROXY_HOPS` correct.
- [ ] `backend/.env` is mode 600, not in git, not readable by other users. `git log -p -- .env` shows nothing.
- [ ] Firewall: only 22, 80 and 443 open. PostgreSQL, 8000 and 3000 not reachable from outside (`nmap` from another machine).
- [ ] SSH: keys only, password login off.
- [ ] TLS test passes (for example `testssl.sh` or SSL Labs) for both hostnames.

### Operations

- [ ] Nightly `pg_dump` of the control database, copied off the server, and a **tested restore**.
- [ ] `CRM_MASTER_KEY` stored in a second place. Restore test: a dump plus the key reproduces working credentials.
- [ ] Uptime monitor on `/readyz`; alert contact verified.
- [ ] Log retention set (`journald` size limits) and someone knows `journalctl -u synthra-api`.
- [ ] Key rotation procedure written and tested on a copy (needs the rotation script, finding 2).
- [ ] Token expiry plan for Instagram (about 60 days) and any Facebook or WhatsApp token that is not permanent.
- [ ] A process for upgrades: backup, `git pull`, install, build, restart, check `/readyz`.

### End-to-end smoke test on the real server

- [ ] Register a new account; the verification email arrives; log in; log out; password reset works.
- [ ] Brute-force check: five wrong passwords lock the account for the configured window.
- [ ] Connect a test Supabase project: test, dry-run, apply, status page shows applied.
- [ ] Configure **only WhatsApp**: nothing about Facebook or Instagram is required.
- [ ] Meta webhook verifies; a real message appears in the inbox and the bot answers; send from the dashboard works.
- [ ] Upload a knowledge base document; the bot answers from it; re-upload of an unchanged file does not re-embed.
- [ ] Facebook and Instagram comment: webhook arrives, reply (public and private), hide, delete (platform first), bulk delete.
- [ ] Register a **second** account and confirm it cannot see anything from the first (try guessing IDs from the first account's URLs).
- [ ] Restart the server mid-conversation; queued events are processed after the restart and not duplicated.

## C. Not done yet in the code

These are known gaps, tracked in `CLAUDE.md`:

- Meta single-app limitation (see above).
- Master key rotation script.
- Instagram token refresh or expiry warning.
- Test coverage for most of the backend and all of the frontend.
- Alerting.
