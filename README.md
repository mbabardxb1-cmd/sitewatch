# SiteWatch

Website uptime monitoring, technical audits, expiry tracking and monthly maintenance reports
for agencies managing many client websites.

> **Build status: Phase 2 of 8 complete** — authentication, role-based access control,
> tenant scoping, full database schema, user management, client and website management,
> and a dashboard built from stored data.
> See [docs/PROPOSAL.md](docs/PROPOSAL.md) for the architecture and phase plan, and
> [docs/PHASES.md](docs/PHASES.md) for what each phase delivered.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router, TypeScript), Tailwind CSS |
| API | FastAPI (Python 3.13), SQLAlchemy 2, Pydantic 2 |
| Database | PostgreSQL 16, Alembic migrations |
| Background jobs | Celery + Redis (from phase 3) |
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

None are required for phases 1–2. Later phases need an SMTP provider (alerts and reports), and
optionally registrar or hosting API credentials. See the proposal, section 6.
