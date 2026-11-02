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

## Phase 3 — Background uptime monitoring, incidents, alerts ✅

**Delivered**
- **SSRF-safe HTTP client** (`app/netsafe/http.py`). DNS is resolved at connect time and the
  connection is refused if *any* answer is private, loopback, link-local, CGNAT or metadata.
  The socket connects to the validated IP (defeats DNS rebinding) while TLS still verifies the
  real hostname. Redirects are followed manually and each hop is re-validated (max 5, loops
  detected). Environment proxies are ignored. The Phase 2 "allow private targets" switch is
  replaced by an explicit, non-production-only `PRIVATE_TARGET_ALLOWLIST` of IP networks.
- **Uptime probe** (`app/monitoring/checker.py`). Records the HTTP status, response time and final
  URL, and classifies failures as timeout, DNS, connect, SSL, unexpected status, redirects or
  blocked.
- **Scheduler**: Celery beat ticks every 15 s. The dispatcher claims due websites with
  `FOR UPDATE SKIP LOCKED` and leases them forward, so websites can't be double-checked. Paused
  and archived websites, and websites of deactivated clients, are skipped. Network I/O happens
  outside any database lock; results are applied under a row lock.
- **State machine** (`app/monitoring/engine.py`):
  - quick retries (`RETRY_DELAY_S`) before an outage is confirmed;
  - incidents start at the first failed check;
  - one "down" and one "recovered" email per incident, with a recovery threshold;
  - one alert per slow-response episode;
  - one alert per window for repeated 5xx errors that never became an outage.
- **Notifications** (`app/notify/`): a `Notifier` interface with an SMTP sender (Slack or webhooks
  can be added as further senders). Recipients come from the website plus global, client and
  website rules; a disabled website rule mutes an event. Optional minimum interval per rule.
  Delivery is de-duplicated by a unique key per event, retried with backoff, and logged as
  sent, failed or suppressed. Nothing is ever silently dropped, e.g. "No recipients configured"
  and "SMTP is not configured" are recorded.
- **Statistics** (`app/monitoring/stats.py`): time-weighted uptime for 24 h, 7 days, 30 days and
  the calendar month, computed in SQL. Gaps without checks count as unknown and are excluded,
  never counted as uptime. Includes coverage, downtime and incident counts, hourly response
  times (average, p95, max), hourly rollups that survive raw-check retention, and a daily
  retention job.
- **API**: `/websites/{id}/uptime`, `/response-times`, `/checks` (staff only), `/check-now`
  (rate-limited), `/uptime/overview`, `/incidents` (+ acknowledge; clients get a cause-free
  view), `/notification-rules`, `/notification-logs` (scoped), `/notification-events`,
  `/notifications/test`. `/api/readyz` reports worker heartbeat and dispatcher age.
- **UI**: Uptime Monitoring page (active incidents with acknowledge, per-site 24 h/7 d/30 d);
  website page with uptime tiles that state their coverage, an hourly response-time chart
  (crosshair tooltip, threshold line, table view), incidents and recent checks (staff); a
  Notifications page (rules, test email, delivery log); real monthly uptime on the dashboard.
- **Compose**: `worker` and a single `beat` service, plus a `mailpit` dev profile.

**Verification**
- `pytest`: 163 tests passing (58 new). Highlights:
  - **Probe against real local HTTP(S) servers**: status codes, configurable expected codes,
    redirects, loops and long chains, redirects to the metadata IP, private ranges and `file://`
    (blocked), slow/degraded, timeouts, refused connections, self-signed TLS, DNS rebinding (a
    hostname resolving to 169.254.169.254), mixed public/private DNS answers, IP pinning,
    environment proxies ignored.
  - **State machine**: retry then confirm, incident starts at the first failure, a transient
    failure never alerts, an ongoing outage alerts once, recovery threshold, a second outage
    gives a new incident, degradation and 5xx alerts once per episode or window, mute rules,
    no-recipient and failed-delivery logging.
  - **Scheduler**: only due, active, client-active websites are claimed; 4 concurrent
    dispatchers × 40 websites → each claimed exactly once; full beat → check → email pipeline
    against a real server.
  - **Statistics**: no data → `null`; gaps counted as unknown, not uptime; open and clipped
    incidents; rollups are idempotent and survive retention.
  - **API**: scoping and 404s, raw checks staff-only, client incident view without causes,
    acknowledge rules, manual-check rate limit; real SMTP delivery to an `aiosmtpd` server.
- **Live run with real processes** (Celery worker + beat, uvicorn, `aiosmtpd`; nobody logged in,
  status read straight from the database):
  1. A site on 127.0.0.1 was registered (60 s interval, 1 retry) and checked automatically.
  2. The site process was stopped: a failed check, a retry 15 s later, outage confirmed, and the
     incident started at the first failure.
  3. 20 s later it was still one incident.
  4. The site was restarted: recovery confirmed and the incident closed (80 s).
  5. The SMTP server received exactly one "down" and one "recovered" email per outage (4 emails
     for 2 outages), with no duplicates.
- **Browser**: dashboard monthly uptime, Uptime page, website page (chart tooltip, manual
  check), Notifications page. The client user sees incidents without causes, has no
  Notifications page, and the API returns 7 client-safe incident fields.
- `alembic check`: no drift; migration 0002 upgrade → downgrade → upgrade verified.

**Limitations / not verified here**
- This sandbox has no direct internet access, so live checks ran only against local servers.
  The public-internet path is the same code (proven by the pinned-connection tests).
- Docker images are still not built here (no Docker daemon); compose config validates.
- Checks run from a single location.

## Phase 4 — Monthly crawler and technical audit engine ✅

**Delivered**
- **Crawler** (`app/audit/crawler.py`): breadth-first from the homepage within depth and page
  limits. It:
  - obeys robots.txt for agent `SiteWatchMonitor` (and `*`), including `Crawl-delay`, and only
    fetches sitemaps robots.txt allows or explicitly declares;
  - rate-limits per host and skips excluded URL patterns (glob or `re:`);
  - checks links beyond the depth limit without parsing them;
  - analyses a redirect target once, under its own URL;
  - checks external links and images with HEAD, falling back to GET (capped per audit);
  - fetches every request through the SSRF-safe client.
- **19 checks** (`app/audit/checks.py`): broken internal/external links, redirect chains and
  loops, access-restricted pages, robots-excluded pages, missing/duplicate titles and meta
  descriptions, broken images, missing alt text, slow pages, robots.txt availability and
  directives (incl. disallow-all), noindex on the homepage or on sitemap URLs, sitemap
  presence/validity/URL errors/robots conflicts/foreign hosts, SSL validity and expiry
  (`app/netsafe/tls.py`), important-page availability, and JavaScript rendering (always reported
  as not checked).
- **Honesty model:** each finding is confirmed, warning or could-not-check; each check records
  checked, partly checked, not checked or not applicable, with a reason. Runs are "partial" when
  anything was limited. Findings are capped per check, with the cap recorded.
- **Issue tracking** (`app/audit/service.py`): new, persisting, recurring (resolved then found
  again) and resolved. Resolution requires verification by a later audit, which is recorded with
  a note. Developer states: acknowledged, in progress, fixed – awaiting verification (re-opened
  automatically if the next audit still sees the problem). Could-not-check findings never become
  issues. Internal notes are staff-only.
- **Scheduling:** an hourly job creates due monthly runs, idempotent via a partial unique index;
  stale queued runs are re-sent. Runs are claimed atomically (queued → running), so a duplicate
  task delivery does nothing; stuck runs are failed after 3 hours. Audits run on their own
  `audit` queue and worker.
- **Notifications:** "audit completed" and "critical audit findings" (new or recurring
  critical/high issues only), using the Phase 3 de-duplication and logging.
- **API:**
  - Audits: `/audits`, `/audits/{id}` (with full coverage), `/findings` (filters), `/compare`,
    `/export?format=csv|json`, `POST /websites/{id}/audits`.
  - Important pages: `/websites/{id}/important-urls`.
  - Issues: `/issues` (client-safe view for clients; informational items hidden), `PATCH`
    (cannot set "resolved"), `/issues/{id}/comments`.
- **UI:**
  - Technical Audits page (history, start audit, auto-refresh).
  - Audit detail page (stats, comparison with the previous audit, check coverage, filterable
    findings, CSV/JSON export).
  - Issues page (filters, status changes, details and internal notes; simplified for clients).
  - Website page: an audit card with open-issue counts, recent audits and the important-pages
    editor.
- **Migration 0003:** finding titles; issue certainty, detail, resolution note and reopen count.

**Verification**
- `pytest`: 185 tests passing (22 new), using local sites with known defects:
  - **Detection:** every defect is found exactly once.
  - **Crawler behaviour:** robots.txt respected (nothing under `/private/` requested; with
    disallow-all, only robots.txt is fetched); depth limit; exclusions; `Crawl-delay` honoured
    over the configured rate (fake clock).
  - **Issue lifecycle:** resolution is verified, with a note; a removed external link is resolved
    as "no remaining references"; a re-broken page recurs; "awaiting verification" re-opens.
  - **No false resolutions:** a partial crawl and an unreachable site resolve nothing.
  - **Scheduling:** the scheduler is idempotent; a duplicate task delivery runs once.
  - **SSL:** a self-signed certificate is reported invalid with its expiry date.
  - **API:** permissions and isolation; client issue fields; "resolved" can't be set by hand;
    assignee rules; notes hidden from clients; CSV formula neutralisation.
- **Live run** with real Celery worker and beat processes:
  - A manual audit started in the browser crawled 11 pages in about 10 seconds; the findings
    matched the site's defects; the CSV export downloaded.
  - A developer moved an issue to "in progress" and added a note. The client saw "Being worked
    on", no notes, and 403 from the audits API.
  - Calling the scheduler twice created 3 runs, then 0. The scheduled re-audit kept the same 11
    issues (no false resolutions). The unreachable sites were reported "partial" with nothing
    checked.
  - Audit emails were sent, or logged as "No recipients configured".

**Limitations**
- JavaScript-rendered pages are not executed (reported as not checked). Playwright could be
  added later as an optional renderer.
- Per-audit caps: 300 external links, 500 images, 100 extra sitemap URLs, 1000 findings per
  check (all reported when hit).
