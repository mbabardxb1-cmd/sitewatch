# SiteWatch

Website uptime monitoring, technical audits, expiry tracking and monthly maintenance reports
for agencies managing many client websites.

> **Build status: Phase 3 of 8 complete** — authentication and role-based access, tenant
> isolation, client and website management, and background uptime monitoring with incidents,
> uptime statistics and email alerts.
> See [docs/PROPOSAL.md](docs/PROPOSAL.md) for the architecture and phase plan, and
> [docs/PHASES.md](docs/PHASES.md) for what each phase delivered.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router, TypeScript), Tailwind CSS |
| API | FastAPI (Python 3.13), SQLAlchemy 2, Pydantic 2 |
| Database | PostgreSQL 16, Alembic migrations |
| Background jobs | Celery + Redis (worker + single beat scheduler) |
| PDFs | ReportLab (from phase 6) |

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
- Optional later: registrar or hosting API credentials (phase 5).
