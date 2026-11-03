# SiteWatch

Website uptime monitoring, technical audits, expiry tracking and monthly maintenance reports
for agencies managing many client websites.

> **Build status: Phase 6 of 8 complete** — authentication and role-based access, tenant
> isolation, client and website management, background uptime monitoring with incidents and
> email alerts, monthly technical audits with issue tracking, domain/hosting/SSL expiry
> monitoring, and automated monthly PDF reports.
> See [docs/PROPOSAL.md](docs/PROPOSAL.md) for the architecture and phase plan, and
> [docs/PHASES.md](docs/PHASES.md) for what each phase delivered.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router, TypeScript), Tailwind CSS |
| API | FastAPI (Python 3.13), SQLAlchemy 2, Pydantic 2 |
| Database | PostgreSQL 16, Alembic migrations |
| Background jobs | Celery + Redis (worker + single beat scheduler) |
| PDFs | ReportLab (DejaVu fonts for Unicode) |

## Repository layout

```
backend/   FastAPI app (app/), Alembic migrations, pytest suite
frontend/  Next.js app
docs/      Proposal, phase log
docker-compose.yml, .env.example
```

## Local development

Prerequisites: Python 3.12+, Node 22, PostgreSQL 16, Redis 7.

```bash
# Databases (adjust to your setup)
createuser -P sitewatch            # password: sitewatch
createdb -O sitewatch sitewatch
createdb -O sitewatch sitewatch_test

# Backend
python -m venv .venv && . .venv/bin/activate
pip install -e "backend[dev]"
cd backend
export COOKIE_SECURE=false         # local plain-http only
alembic upgrade head
python -m app.cli create-admin --email you@example.com
uvicorn app.main:app --reload --port 8000

# Background monitoring (two more terminals)
celery -A app.worker.celery_app worker -Q uptime,notify,default -l info
celery -A app.worker.celery_app worker -Q audit -c 2 -l info   # crawls
celery -A app.worker.celery_app beat -l info        # run exactly ONE beat

# Frontend (second terminal)
cd frontend
npm ci
npm run dev                        # http://localhost:3000, proxies /api to :8000
```

API docs (non-production only): http://localhost:8000/api/docs

### Tests

```bash
cd backend && pytest               # needs Postgres (sitewatch_test) and Redis db 15
cd frontend && npm run typecheck && npm run build
```

Override test connections with `TEST_DATABASE_URL` / `TEST_REDIS_URL`.

## Docker

```bash
cp .env.example .env               # set strong POSTGRES_PASSWORD and matching DATABASE_URL
docker compose up -d --build
docker compose run --rm api python -m app.cli create-admin --email you@example.com
```

The web UI listens on `127.0.0.1:3000`. HTTPS termination (Caddy) is added in phase 8; until
then put the app behind your own TLS proxy — session cookies are `Secure` and will not be sent
over plain HTTP.

## Uptime monitoring — how it works

1. **Beat** ticks every 15 s and runs the dispatcher. The dispatcher claims due websites with
   `SELECT … FOR UPDATE SKIP LOCKED` and pushes their `next_check_at` forward, so a website can't
   be checked twice even if several dispatchers run.
2. A **worker** performs the HTTP check via the SSRF-safe client: DNS is validated at connect
   time, the socket is pinned to the validated IP, and every redirect hop is re-validated
   (maximum 5 hops; loops detected).
3. The **state machine** (`app/monitoring/engine.py`) records the check, then:
   - On a failure, it retries after `RETRY_DELAY_S`. The outage is confirmed only after
     `retry_attempts` retries also fail. The incident starts at the first failed check.
   - It sends one "down" email per incident and one "recovered" email after
     `recovery_threshold` successes. Further failures never re-alert.
   - It alerts once per episode for slow responses (`DEGRADED_ALERT_AFTER`) and for repeated
     5xx errors that never became an outage.
4. **Notifications** are written to `notification_logs` with a unique de-duplication key, then
   delivered by a separate task with retries. A safety-net job re-queues anything stuck.
5. **Uptime %** is time-weighted from actual checks. Time with no checks (worker down, monitoring
   paused) is reported as *unknown* coverage and never counted as uptime.

Health: `GET /api/readyz` reports database, Redis, the worker heartbeat age and the last
dispatcher run.

**Limitation:** checks run from a single location. A network problem between the server and a
site will be reported as downtime.

## Technical audits — how they work

- **Scheduling:** each website has an audit day (1–28). An hourly job creates that month's
  scheduled run once the day arrives; a unique index on (website, month) makes duplicates
  impossible. Manual audits can be started from the UI or API (one at a time per website).
- **Crawler** (`app/audit/crawler.py`): starts at the homepage, breadth-first within the
  configured depth and page limits. It obeys robots.txt (including `Crawl-delay`), waits between
  requests (the website's requests-per-second setting; 1 request/s per external host), skips
  excluded URL patterns, and uses the same SSRF-safe client as uptime checks. Sitemaps are parsed
  with `defusedxml`.
- **Checks** (`app/audit/checks.py`): 19 checks. Each finding is *confirmed*, *warning* (e.g. a
  timeout that may be transient) or *could not check* (e.g. a site that blocks bots). Each check
  records its coverage: checked, partly checked (with the reason, e.g. "stopped at the page
  limit"), not checked (with the reason) or not applicable. JavaScript rendering is not
  implemented and is always reported as not checked.
- **Issues:** confirmed and warning findings become issues tracked across audits as new,
  persisting, recurring or resolved. An issue is resolved **only** by an audit that re-checked it,
  or that crawled the whole site and found no remaining references to the URL. Developers can
  mark "fixed – awaiting verification", but cannot set "resolved" themselves.
- **Exports:** CSV (protected against spreadsheet formula injection) and JSON with full coverage.

## Domain, hosting and SSL expiry

- **Domains:** the registrable domain is derived from the website URL using the bundled Public
  Suffix List (e.g. `shop.example.co.uk` → `example.co.uk`), or set manually. It is looked up
  through **RDAP** (via the IANA bootstrap file), with **WHOIS** (port 43) as a fallback for TLDs
  without RDAP. Lookups are refreshed daily within 60 days of expiry, weekly otherwise, and
  retried 6 hours after a failure.
- **Manual dates** are stored separately from looked-up dates, so a lookup never overwrites what
  a person typed. They are always labelled *Manual entry (not verified)*, and are used only while
  no registry lookup has succeeded.
- **Hosting:** renewal dates are entered manually unless a provider integration verifies them.
  No provider integrations ship by default; see `app/expiry/hosting.py` for the adapter
  interface. Provider credentials are encrypted with `ENCRYPTION_KEY` (Fernet) and never returned
  by the API. Nothing recorded → **Unknown**.
- **SSL:** certificates are checked daily (every 6 hours in the last 14 days) through the
  SSRF-safe connector. A check that cannot connect is recorded as such and never replaces the
  last real observation.
- **Alerts:** domain and hosting at 60/30/14/7 days, SSL at 30/14/7/3/1 days, plus
  "expired" and "certificate not valid". Thresholds can be overridden per website, client or
  globally with a notification rule (`{"days_before": [...]}`). Each threshold alerts once per
  expiry date; a renewal starts a new cycle.
- **Clients** see expiry information only if an administrator enabled it for their organization,
  and never see account references, internal notes or raw lookup data.

## Monthly PDF reports

- **Two reports per website per month:** a *technical* report for the team and a *client*
  report. They are generated automatically for the previous month once the configured day
  arrives (setting `reports.generation_day`, default 2), and can also be generated on demand for
  any past month or the current month so far.
- **Client reports must be published** (by an administrator) before client users can see them;
  they can be emailed to the website's recipients with the PDF attached. Regenerating a report
  withdraws its publication.
- **Contents:** cover with logo, website, period and generation date; overall status with the
  reasons behind it; executive summary; (1) monitoring results — availability, outages, daily
  response-time chart, audit results with check coverage, issues (new / fixed-and-verified /
  recurring / outstanding), domain/hosting/SSL with sources; (2) maintenance work performed,
  recorded separately by the team; (3) evidence-based recommendations; (4) comparison with the
  previous month; (5) notes on method. Page numbers "Page X of Y".
- **Honesty rules:** missing data is stated as missing; "fixed" only means verified by a later
  audit; an audit that could not reach the site says so instead of reporting "no issues";
  client reports contain no internal notes, raw errors, incident causes or account references.
- **Storage:** files live under `STORAGE_DIR`, written atomically, with a SHA-256 checksum
  verified on download. Every download is recorded in the audit log.
- **Branding:** company name, accent colour, footer text and logo (PNG/JPEG ≤ 1 MB, validated
  and re-encoded) on the Reports page.

## Security model (implemented)

- **Passwords**: argon2id; minimum 12 chars with mixed case and a digit; transparent rehash.
- **Sessions**: random 256-bit token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie; only the
  SHA-256 hash is stored. 12 h absolute lifetime; revoked on logout, password change, role change
  and deactivation.
- **CSRF**: every state-changing request must echo the per-session token in `X-CSRF-Token`.
- **Login protection**: Redis rate limits per IP and per email, plus temporary account lockout.
  Identical error for unknown user and wrong password.
- **Authorization**: permission checks on every endpoint (`require(...)`), with roles defined in
  `backend/app/auth/permissions.py`. Website-owned data is filtered by `app/auth/scope.py`;
  client users are additionally pinned to their own organization. Out-of-scope IDs return 404.
- **Audit trail**: logins, lockouts, user/role changes are written to `audit_log`.

## Roles

| Capability | Super Admin | Developer | Client |
|---|:-:|:-:|:-:|
| Manage users, settings, integrations | ✓ | | |
| See all websites | ✓ | assigned | assigned & own org |
| Technical logs, raw checks, internal notes | ✓ | ✓ | |
| Run checks/audits, manage issues | ✓ | ✓ | |
| Approved client reports | ✓ | ✓ | ✓ |
| Expiry info | ✓ | ✓ | if enabled for their org |

## Third-party services

- **SMTP (required for alerts):** any provider; set the `SMTP_*` variables. Until it's configured,
  alerts are recorded with status "failed" ("SMTP is not configured"); nothing is silently lost.
  For local development, `docker compose --profile dev up` starts Mailpit to catch emails.
- **Outbound access for expiry lookups:** the server must reach `data.iana.org` and the RDAP
  servers it lists (HTTPS), and WHOIS servers on TCP port 43 for TLDs without RDAP.
- Optional: hosting provider API credentials, once a provider adapter is added.
