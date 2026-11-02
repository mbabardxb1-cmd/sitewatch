# Phase log

## Phase 1 — Foundation, auth, RBAC ✅

**Delivered**
- Project structure (backend / frontend / docs), Dockerfiles, docker-compose, `.env.example`.
- Complete relational schema for all 8 phases (26 tables) as SQLAlchemy models plus the
  initial Alembic migration. Includes the uniqueness guards needed later: one open incident per
  website, one scheduled audit per website per month, one report per website/period/kind, and
  notification de-duplication keys.
- Roles and permissions catalogue (`app/auth/permissions.py`), seeded by migration and syncable
  with `python -m app.cli sync-permissions`.
- Session authentication with CSRF protection, rate limiting, lockout and session revocation.
- Tenant scoping helpers used by every website-owned query from phase 2 on.
- User management API (`/api/v1/users`) and UI: create, edit, change role, deactivate/activate.
  Safeguards: can't deactivate yourself, and the last active super admin can't be removed.
- Health endpoints `/api/healthz` and `/api/readyz` (DB + Redis).
- Next.js shell: login, responsive navigation filtered by permission (sections not yet built
  are marked "Soon" rather than showing mock data), dashboard placeholder, Users page.

**Verification**
- `pytest`: 37 tests passing (auth flows, cookies, CSRF, rate limits, lockout, session expiry
  and revocation, user admin validation, role catalogue, cross-tenant scoping including a
  mis-assigned client user).
- Migration upgrade → downgrade → upgrade on a clean database; `alembic check` reports no drift.
- Frontend `tsc` and `next build` pass; `npm audit` reports 0 vulnerabilities.
- Browser end-to-end test (Playwright): redirect to login when signed out, error on a bad
  password, admin login, creating a user through the UI, developer has no Users nav item and
  gets 403 calling `GET /api/v1/users` directly, mobile layout.

**Not verified in this environment**
- `docker compose up`: no Docker daemon in the build sandbox. The compose file passes
  `docker compose config` and the backend packages cleanly; the image build is first verified
  in phase 8.

## Phase 2 — Clients, websites, dashboard ✅

**Delivered**
- **Clients API** (`/api/v1/clients`): list (admins + developers), create/edit/deactivate (admins).
  Per-client switch controlling whether client users may see expiry information (used in phase 5).
  Deactivating a client blocks its users immediately (existing sessions and new logins).
- **Websites API** (`/api/v1/websites`): list with search/status/client filters, create, edit,
  pause/resume, archive/restore (history kept), crawl configuration, user assignments.
  - URL safety at registration (`app/netsafe/urls.py`): http/https only, no embedded credentials,
    no localhost/internal hostnames, no private/loopback/link-local/CGNAT/metadata IPs (IPv4,
    IPv6, IPv4-mapped IPv6), IDN normalised to punycode. A guarded `ALLOW_PRIVATE_TARGETS`
    setting exists for local testing and is ignored in production.
  - Validation: check interval 1–5 min, retries 0–10, status codes 100–599, audit day 1–28,
    recipient emails, crawl limits (depth ≤ 10, pages ≤ 10,000, ≤ 10 req/s), exclusion
    regexes compiled before saving, unknown crawl options rejected.
  - Duplicate active URLs per client are refused (409).
  - A developer who registers a site is automatically assigned to it. Only admins assign people;
    a client user can only be assigned to their own organization's websites.
  - Editing an archived site is refused; changing a URL resets state to Unknown because
    earlier results described a different address.
- **Role-shaped responses**: client users receive `WebsiteClientView`, which has no
  configuration, recipients, assignments or hostnames at all.
- **Health labels** Healthy/Warning/Critical/Unknown always come with the reason behind them
  (e.g. "Outage confirmed after retries", "No completed checks yet").
- **Dashboard summary** (`/api/v1/dashboard/summary`): totals, online/offline/degraded/unknown/paused
  counts, 24-hour average response time, active incidents, all limited to the user's websites.
  Monthly uptime is `null` ("No data") until phase 3 produces history; nothing is estimated.
- **UI**: Dashboard, Websites list (responsive), Add website, Website detail (status, monitoring
  settings, crawl configuration, assignments, pause/archive), Clients page, client-user creation
  on the Users page. Client users see a simplified dashboard and website page.

**Verification**
- `pytest`: 105 tests passing (68 new). They cover URL safety (32 accepted/rejected URL cases),
  website validation, duplicates and archive/restore, assignment rules, client-user write
  attempts (403), developer edits to unassigned sites (404), out-of-scope IDs answering exactly
  like non-existent ones, client responses free of technical fields (list, detail, dashboard),
  dashboard counts excluding out-of-scope data, and locking out users of a deactivated client.
- `alembic check`: no schema changes were needed in this phase.
- Browser end-to-end run (Playwright): admin creates a client, a client user and a website (an
  internal URL is refused, the URL is normalised); edits monitoring and crawl settings; assigns
  a developer and the client user. The client user sees a simplified dashboard and page; their
  direct API `PATCH` returns 403, another site returns 404, and their payload holds only the
  11 client-safe fields. The developer sees the assigned site. No horizontal overflow at 390 px.

**Carried to phase 3**
- Websites of a deactivated client should be skipped by the monitoring scheduler.
- DNS-level SSRF checks (resolved-IP validation, pinned connections, redirect re-validation).
