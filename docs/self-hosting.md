# Self-hosting and deployment (no Docker)

This guide installs Synthra CRM on a single Linux server (examples use Ubuntu 24.04) with systemd and Caddy. Nothing here needs Docker.

The server runs three things: the **API** (FastAPI, with the queue worker inside the same process), the **dashboard** (Next.js) and a **reverse proxy** (Caddy) that terminates HTTPS. A PostgreSQL database holds accounts, sessions, encrypted settings and the webhook queue (the "control database"). Customers' own databases are separate and are never on your server.

Sizing: 2 vCPU and 2 GB RAM is a reasonable start. The embedding model uses roughly 300-500 MB of RAM once loaded. Add swap on very small machines.

## 0. Before you start

- A server with a public IP, and a domain you control.
- Two DNS `A` records pointing to the server: for example `app.example.com` (dashboard) and `api.example.com` (API). They **must share a registrable domain** because the login cookie is `SameSite=strict`.
- An SMTP account for verification and reset emails (required in production).
- Read [environment.md](environment.md) so you know what each variable does.

## 1. Server preparation

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ufw python3 python3-venv python3-pip build-essential debian-keyring debian-archive-keyring apt-transport-https

# Node.js 22 (any version >= 20.9 works)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Unprivileged service user and app folder
sudo useradd --system --create-home --home-dir /home/synthra --shell /usr/sbin/nologin synthra
sudo mkdir -p /opt/synthra-crm && sudo chown synthra:synthra /opt/synthra-crm
```

Check `python3 --version` is 3.11, 3.12 or 3.13. Ubuntu 24.04 ships 3.12.

Firewall: only SSH, HTTP and HTTPS are public. The API (8000) and dashboard (3000) listen on `127.0.0.1` only.

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

## 2. Control database (PostgreSQL)

Use either a managed PostgreSQL (Supabase, Neon, RDS, ...) and copy its connection URL, or install it locally:

```bash
sudo apt install -y postgresql postgresql-contrib
sudo apt install -y postgresql-16-pgvector      # package name follows your PostgreSQL major version
sudo -u postgres psql <<'SQL'
create role synthra login password 'CHANGE_ME_LONG_RANDOM';
create database synthra_crm owner synthra;
SQL
```

URL: `postgresql://synthra:CHANGE_ME_LONG_RANDOM@localhost:5432/synthra_crm`. If the password contains special characters, percent-encode them in the URL.

Do not open port 5432 to the internet.

## 3. Get the code and install the backend

```bash
sudo -u synthra git clone <your-repo-url> /opt/synthra-crm
cd /opt/synthra-crm && sudo -u synthra git checkout main     # or your release branch

cd backend
sudo -u synthra python3 -m venv .venv
sudo -u synthra .venv/bin/pip install -r requirements.txt
```

Configuration (production template at the project root):

```bash
sudo -u synthra cp /opt/synthra-crm/.env.example /opt/synthra-crm/backend/.env
sudo chmod 600 /opt/synthra-crm/backend/.env
sudo -u synthra nano /opt/synthra-crm/backend/.env
```

Fill at least `CRM_DATABASE_URL`, `CRM_MASTER_KEY`, `CRM_JWT_SECRET`, `CRM_PUBLIC_URL`, `CRM_API_PUBLIC_URL`, `CRM_TRUSTED_PROXY_HOPS=1` and the `CRM_SMTP_*` values. Generate the keys with:

```bash
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

**Back up `CRM_MASTER_KEY` somewhere other than this server** (password manager). If it is lost, every saved token, API key and database URL becomes unreadable and every user has to re-enter them.

Production mode refuses to start with a non-`https://` `CRM_PUBLIC_URL`, or without SMTP while email verification is on. That is intentional.

## 4. API service

```bash
sudo cp /opt/synthra-crm/deploy/synthra-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now synthra-api
sudo systemctl status synthra-api
journalctl -u synthra-api -f
```

On first start the API applies the control-plane migrations (`CRM_AUTO_MIGRATE=true`) and, with `CRM_EMBEDDER=fastembed`, downloads the embedding model (about 90 MB) into `/var/lib/synthra/models` the first time the knowledge base is used. The server needs outbound internet access for that.

Check it locally:

```bash
curl -s http://127.0.0.1:8000/healthz     # {"status":"ok"}
curl -s http://127.0.0.1:8000/readyz      # {"status":"ready"}
```

## 5. Dashboard service

`NEXT_PUBLIC_API_URL` is compiled into the browser bundle, so create the file **before** building, and rebuild whenever it changes.

```bash
cd /opt/synthra-crm/frontend
echo "NEXT_PUBLIC_API_URL=https://api.example.com" | sudo -u synthra tee .env.production
sudo -u synthra npm ci
sudo -u synthra npm run build

sudo cp /opt/synthra-crm/deploy/synthra-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now synthra-web
```

If `which npm` is not `/usr/bin/npm`, edit `ExecStart` in the unit file.

Optionally set `NEXT_PUBLIC_DOCS_URL` in the same file (before building) so channel pages link to the setup guides.

## 6. HTTPS reverse proxy (Caddy)

Install Caddy from its official apt repository (see https://caddyserver.com/docs/install#debian-ubuntu-raspbian), then:

```bash
sudo cp /opt/synthra-crm/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile            # replace app.example.com and api.example.com
sudo systemctl reload caddy
```

Caddy obtains and renews certificates automatically once DNS points at the server and ports 80/443 are reachable.

Verify from your own machine:

```bash
curl -s https://api.example.com/readyz
```

Then open `https://app.example.com`, register, and verify the email.

## 7. Connect Meta

Each channel's page in the dashboard shows the exact webhook URL and verify token. A workspace has **three webhooks**, one per platform. Meta allows only one callback URL per object, so Facebook inbox and comments share one webhook, and so do Instagram inbox and comments:

```
https://api.example.com/webhooks/<workspace-id>/whatsapp
https://api.example.com/webhooks/<workspace-id>/facebook
https://api.example.com/webhooks/<workspace-id>/instagram
```

Follow the guide for each channel: [WhatsApp](channels/whatsapp.md), [Facebook inbox](channels/facebook-inbox.md), [Facebook comments](channels/facebook-comments.md), [Instagram inbox](channels/instagram-inbox.md), [Instagram comments](channels/instagram-comments.md).

Meta only delivers webhooks to public HTTPS URLs with a valid certificate. Plain HTTP and self-signed certificates are rejected.

## 8. Upgrades

```bash
cd /opt/synthra-crm && sudo -u synthra git pull
cd backend && sudo -u synthra .venv/bin/pip install -r requirements.txt
cd ../frontend && sudo -u synthra npm ci && sudo -u synthra npm run build
sudo systemctl restart synthra-api synthra-web
```

- **Control database:** new migrations are applied automatically at startup when `CRM_AUTO_MIGRATE=true`. If it is `false`, the API refuses to start while migrations are pending.
- **Workspace databases:** each customer upgrades their own database from Dashboard > Settings > Database, with a dry-run preview first. Migrations are idempotent and versioned.
- Take a control database backup before every upgrade.

## 9. Backups and monitoring

```bash
# Nightly dump of the control database (add to the postgres user's crontab, keep copies off the server)
pg_dump -Fc synthra_crm > /var/backups/synthra_crm_$(date +%F).dump
```

Back up `backend/.env` (or at least `CRM_MASTER_KEY`) separately from the dumps. A dump without the key contains only encrypted credentials.

- Logs: `journalctl -u synthra-api -u synthra-web -u caddy`. Logs never include tokens or message bodies, but they do include request paths and IDs.
- Uptime: point an external monitor at `https://api.example.com/readyz`. Treat anything other than `{"status":"ready"}` (including an error response) as down.
- Webhook backlog: the API exposes queue status at `GET /api/channels/-/queue` (authenticated). Check that the dashboard surfaces it before relying on it.

## 10. Scaling notes

- The queue uses `FOR UPDATE SKIP LOCKED`, so several API processes can share one control database safely. Run extra API-only replicas with `CRM_RUN_WORKER=false`, or raise `CRM_WORKER_CONCURRENCY`.
- Each process keeps its own cache of workspace database connections (up to 50 workspaces, 4 connections each). Mind the connection limits of your customers' databases (Supabase free tier is low); idle pools close after 10 minutes.
- Put the control database on a managed service once you have paying users, and enable its point-in-time recovery.

## 11. Hosting without managing a server

Any platform that can run a long-lived Python process and a Node process, give both environment variables, and expose them over HTTPS can host this (for example a Platform-as-a-Service with native buildpacks). Requirements:

- API start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, working directory `backend/`, build command `pip install -r requirements.txt`.
- Dashboard: build `npm ci && npm run build`, start `npm run start`, with `NEXT_PUBLIC_API_URL` present at **build** time.
- The worker runs inside the API process, so the API must stay running (not scale to zero), or webhooks will queue until it wakes.
- Persist the embedding model directory (`FASTEMBED_CACHE_PATH`) or accept a re-download on each deploy.
- Set `CRM_TRUSTED_PROXY_HOPS` to the number of proxies the platform places in front of the API; check the platform's documentation. A wrong value weakens rate limiting.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| API exits with "migration(s) pending" | `CRM_AUTO_MIGRATE` is `false`; set it to `true` or apply migrations once. |
| API exits with a validation error naming a variable | A key is not base64 of 32 bytes, `CRM_JWT_SECRET` is under 32 characters, or `CRM_PUBLIC_URL` is not https in production. |
| Login works but the session ends on refresh | Dashboard and API are not on the same site, or `CRM_PUBLIC_URL` differs from the origin in the browser. |
| "CSRF" or "Origin not allowed" errors | `CRM_PUBLIC_URL` (or `CRM_ALLOWED_ORIGINS`) does not exactly match the dashboard origin, including `https://` and no trailing path. |
| Dashboard calls `localhost:8000` | `NEXT_PUBLIC_API_URL` was missing at build time. Set it and rebuild. |
| Meta "callback verification failed" | Wrong verify token, URL not public HTTPS, or API not reachable. Test the URL from outside the server. |
| Webhooks return 403 | Wrong app secret saved for that channel, or the workspace/channel is not configured. 403 is deliberately the same for every failure. |
| Rate limiting hits everyone as one user | `CRM_TRUSTED_PROXY_HOPS` is `0` behind a proxy. Set `1` for Caddy. |
| Knowledge base indexing fails on first use | No outbound internet for the model download. Allow it, or use `CRM_EMBEDDER=hash` for testing only. |
