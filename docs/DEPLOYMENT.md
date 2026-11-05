# Deploying SiteWatch

This guide installs SiteWatch on a single Linux server with Docker Compose. Everything runs in
containers; only the Caddy reverse proxy is reachable from the internet (ports 80 and 443).

```
Internet ──► Caddy (HTTPS, automatic certificates)
               ├── /api/*  ──► api (FastAPI)
               └── /*      ──► web (Next.js)
             api, worker, worker-audit, beat ──► PostgreSQL, Redis, /data volume (PDFs, logo)
```

## 1. Requirements

| Item | Minimum | Notes |
|---|---|---|
| Server | 1 vCPU, 4 GB RAM, 40 GB disk (set `WORKER_CONCURRENCY=4`) | Measured: about 2 GB RAM with default settings. Comfortable for ~100 websites; use 2+ vCPU / 8 GB for more. |
| OS | Any Linux with Docker Engine 24+ and the Compose plugin | Tested with Docker 29. |
| DNS | An A/AAAA record (e.g. `monitor.your-agency.example`) pointing to the server | Needed before the first start so Caddy can obtain a certificate. |
| Firewall | Inbound 80/tcp, 443/tcp, 443/udp | Nothing else needs to be public. |
| Outbound | HTTPS to monitored websites, `data.iana.org` and RDAP servers; TCP 43 (WHOIS); your SMTP server | Monitoring and lookups come from this server. |
| Email | An SMTP account (Postmark, SES, SendGrid, Mailgun, Microsoft 365, …) | Without it, alerts and reports are logged as "failed", not lost. |

## 2. Install

```bash
git clone <your-repository-url> sitewatch
```

```bash
cd sitewatch
```

```bash
cp .env.example .env
```

Edit `.env`. At minimum, set these values:

| Setting | Value |
|---|---|
| `SITE_DOMAIN`, `ACME_EMAIL` | your public hostname and a contact address for Let's Encrypt |
| `POSTGRES_PASSWORD` and the same password inside `DATABASE_URL` | a long random string (`openssl rand -base64 30`) |
| `ENCRYPTION_KEY` | `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `SMTP_*`, `SMTP_FROM` | your email provider's settings |
| `APP_BASE_URL` | `https://<SITE_DOMAIN>` (used for links in emails) |

Keep `COOKIE_SECURE=true` and `ENV=production`. The application refuses to start with insecure
production settings (for example `COOKIE_SECURE=false` or the example database password), and
warns about weaker ones. You can check your settings at any time:

```bash
docker compose run --rm migrate python -m app.cli check-config
```

## 3. First start

```bash
docker compose up -d --build
```

The `migrate` service checks the configuration and applies database migrations, and then the
other services start. Wait until everything is healthy:

```bash
docker compose ps
```

Create the first administrator (you will be asked for a password: 12+ characters, mixed case and
a digit):

```bash
docker compose exec api python -m app.cli create-admin --email you@your-agency.example
```

Open `https://<SITE_DOMAIN>` and sign in. Then:
1. **Settings:** check the report day and retention, and upload your logo and branding.
2. **Notifications:** send a test email to confirm SMTP works.
3. **Clients:** add your client organizations; **Users:** add developers and client users.
4. **Websites:** register websites, set alert recipients, and assign developers and client users.

## 4. Updating to a new version

```bash
docker compose --profile ops run --rm backup
```

```bash
git pull
```

```bash
docker compose up -d --build
```

Migrations run automatically through the `migrate` service before the application restarts.
Check `docker compose ps` and `https://<SITE_DOMAIN>/api/readyz` afterwards.

## 5. Backups and restore

**What is backed up:** the PostgreSQL database (all settings, monitoring history, audits, issues,
users) and the `appdata` volume (report PDFs and the logo).

```bash
docker compose --profile ops run --rm backup
```

This writes `backups/sitewatch-<timestamp>/` containing `database.dump`, `files.tar.gz` and
`SHA256SUMS`. Backups older than `BACKUP_KEEP_DAYS` (default 14) are pruned.

**Schedule it daily** with the host's cron (adjust the path):

```cron
17 2 * * * cd /opt/sitewatch && docker compose --profile ops run --rm backup >> /var/log/sitewatch-backup.log 2>&1
```

**Copy backups off the server.** A backup on the same disk does not survive the loss of that
disk. Use your provider's object storage or `rsync` to another machine.

**Restore** (this replaces the current database and files):

```bash
docker compose stop api worker worker-audit beat web
```

```bash
docker compose --profile ops run --rm -e RESTORE_CONFIRM=yes restore /backups/sitewatch-<timestamp>
```

```bash
docker compose up -d
```

The restore verifies the checksums first and refuses to run without `RESTORE_CONFIRM=yes`.
Restoring onto a new server works the same way: install as in section 2 with the same
`ENCRYPTION_KEY`, copy the backup folder into `backups/`, run `docker compose up -d db`, then
follow the three commands above. **Keep a copy of `.env` (or at least `ENCRYPTION_KEY`) in your
password manager:** without it, stored hosting-provider credentials cannot be decrypted.

## 6. Monitoring SiteWatch itself

- `https://<SITE_DOMAIN>/api/readyz` returns `200` when the database and Redis are reachable.
  Its `checks` also report the worker heartbeat and scheduler ages; `"background": "stale"`
  means monitoring is not running. Point an external uptime monitor at this URL.
- `docker compose ps` shows the health check of every service. The workers are pinged with
  Celery; the scheduler (`beat`) must have dispatched within 2 minutes.
- Staff dashboards show "Uptime checks are overdue…" if monitoring stops.
- **Logs:** `docker compose logs -f api worker`. Lines are JSON, with a `request_id` that is also
  returned in the `X-Request-ID` response header. Query strings are never logged.
- **Errors:** set `SENTRY_DSN` to send exceptions to Sentry. No cookies, emails or request bodies
  are sent.
- **Security log:** Settings → Security log lists sign-ins, lockouts and every administrative
  change.

## 7. Building behind a corporate proxy

If the build machine reaches the internet through a TLS-intercepting proxy, set in `.env`:

```dotenv
EXTRA_CA_FILE=/path/to/proxy-ca.pem
BUILD_HTTPS_PROXY=http://proxy.internal:3128
BUILD_NETWORK=host
```

The CA is passed as a BuildKit secret: it is used during the build only and is not stored in
the images. If the Debian package mirror is unreachable, add `INSTALL_FONTS=false`. PDF reports
then use Helvetica, which covers Western European text, and other characters are replaced.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| Browser shows a certificate error | DNS for `SITE_DOMAIN` must point to this server and ports 80/443 must be open; see `docker compose logs caddy`. |
| Login works over HTTP but not HTTPS (or the reverse) | The app only works over HTTPS in production (secure cookies). Always use `https://`. |
| "Too many login attempts" | Wait 15 minutes, or an administrator can re-activate the account from Users. |
| Alerts not arriving | Notifications → Delivery log shows the reason ("SMTP is not configured", "No recipients configured", or the SMTP error). |
| Everything shows "Unknown / No recent checks" | `worker` or `beat` is down: `docker compose ps`, `docker compose logs beat worker`. |
| Domain expiry "Unknown" | The server needs outbound HTTPS to `data.iana.org` and the registry's RDAP server (and TCP 43 for WHOIS-only TLDs). |
| `migrate` exits with a configuration error | Read the message: it names the unsafe setting to fix in `.env`. |
