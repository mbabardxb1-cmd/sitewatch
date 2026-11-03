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

## Phase 5 — Domain, hosting and SSL expiry monitoring ✅

**Delivered**
- **Domain lookups** (`app/expiry/lookup.py`):
  - registrable domain from the bundled Public Suffix List (`tldextract`, no runtime downloads);
  - RDAP via the IANA bootstrap (cached 24 h), parsed per RFC 9083 (expiration/registration
    events, registrar vCard, nameservers);
  - WHOIS fallback over port 43 (IANA referral, then the TLD server) only when RDAP has no
    service or no expiry date; partial RDAP facts are kept;
  - "not registered" is detected for both RDAP and WHOIS.
  - Every connection goes through the SSRF guard.
- **Effective dates** (`app/expiry/service.py`):
  - Domain: the lookup's date (with source and "last verified"); else the manual entry; else
    Unknown. A failed refresh keeps the last verified value, with a note. Manual entries live in
    separate columns and are never overwritten by lookups.
  - Hosting: API-verified, else Manual, else Unknown. A person typing a date always makes it
    Manual.
  - SSL: the latest completed check; "could not connect" attempts are recorded but don't
    replace it.
- **Hosting adapters** (`app/expiry/hosting.py`): a provider interface and registry, with
  Fernet-encrypted credentials. No providers are installed, as decided; the API says so and
  points to manual dates.
- **Schedules:** an hourly refresh of what's due (domains daily near expiry or weekly; failed
  lookups retried after 6 h; SSL daily, or every 6 h near expiry; hosting APIs daily) and hourly
  alerts.
- **Alerts:** each threshold fires once per expiry date (only the tightest one crossed), plus
  "expired" and "certificate not valid". Thresholds can be overridden by notification rules
  (website > client > global). Emails state the source (e.g. "entered manually (not verified)").
- **API:**
  - `/expiry/overview` and `/websites/{id}/expiry`.
  - `PUT /websites/{id}/domain` (domain override, manual date and registrar) and
    `/domain/refresh`.
  - `PUT /websites/{id}/hosting` and `/hosting/refresh`; `/ssl/refresh`.
  - `/integrations/hosting` (admin; credentials never returned).
  - Refreshes are rate-limited. Clients get only effective dates, and only when enabled for
    their organization.
- **UI:**
  - Domain & Hosting page (soonest first, source badges, Unknown with reasons).
  - SSL Certificates page (invalid first).
  - Website page: expiry section with lookup details, a manual-entry form, a hosting form,
    "check now" buttons and certificate history (staff); read-only for clients.
- **Migration 0004:** lookup verification time, domain override, manual fields, hosting entry
  time, SSL check errors.

**Verification**
- `pytest`: 216 tests passing (31 new):
  - RDAP and WHOIS against local servers using the standard formats; bootstrap caching;
    unregistered domains; RDAP without expiry; `.uk` via WHOIS only; WHOIS disabled; IP
    addresses.
  - A failed refresh keeps verified data; manual entries are separate; a domain override resets
    the lookup.
  - Alerts: 25 days left gives one "30" alert, then 13 days gives "14", then expired; a renewal
    restarts the cycle; rule overrides; manual hosting is labelled unverified.
  - SSL: an invalid certificate alerts once; a failed check doesn't erase the last result.
  - Scheduling rate limits; an encrypted provider round trip with a test adapter; missing
    `ENCRYPTION_KEY` → 503.
  - Client visibility on/off, with no account reference, notes or lookup data in client
    responses; write permissions.
- **Live run** with the real worker: `refresh_expiry` looked up 4 domains and 2 certificates.
  Of 3 alerts, 3 were sent, and an immediate second run sent 0. The browser showed source badges
  and Unknown states; the client view had no edit controls and no sensitive fields.

**Not verified here: the public registries.** This sandbox's network policy blocks
`data.iana.org` and the RDAP servers, so the live run used local stand-ins at
`127.0.0.1:8093` (RDAP) and `:8043` (WHOIS), configured through `RDAP_BOOTSTRAP_URL`,
`WHOIS_SERVER_IANA` and `WHOIS_PORT`. The demo registrar is named "Stand-in Registrar (local
demo)". Real lookups need outbound HTTPS to `data.iana.org` and RDAP servers, and TCP 43 for
WHOIS, in the production environment.

## Phase 6 — Automated monthly PDF reports ✅

**Delivered**
- **Snapshot-based reports** (`app/reports/data.py`): every fact is assembled once into a JSON
  snapshot stored with the report, and the PDF is rendered only from it. The snapshot covers:
  - uptime this month and last, outages, monitoring coverage;
  - daily response times (falling back to hourly rollups);
  - the latest audit with grouped coverage limitations;
  - issues: new, fixed (verified), recurring and outstanding;
  - domain, hosting and SSL with their sources;
  - maintenance work.
  The overall status is a label with listed reasons, and recommendations each cite their
  evidence.
- **PDF** (`app/reports/pdf.py`, ReportLab):
  - logo, branded header and footer, "Page X of Y";
  - status box with a text label, tables and severity labels;
  - a single-series daily response chart;
  - sections in order: monitoring results, maintenance performed, recommendations, comparison
    with the previous month, method notes.
  All crawled or user text is escaped; DejaVu fonts give full Unicode, with a safe fallback.
- **Client vs technical reports:** client reports omit incident causes, internal maintenance,
  informational issues, URLs, account references and raw errors (replaced with plain wording).
  Expiry details are included only if enabled for the client organization.
- **Honesty fixes found during review:**
  - downtime is described as "observed while monitoring was running" when coverage is partial;
  - an audit that couldn't reach the site says so, and never reports "no issues";
  - an ongoing outage is called out;
  - identical coverage limitations are grouped.
- **Lifecycle** (`app/reports/service.py`):
  - unique (website, period, kind) rows; the scheduler is idempotent from the configured day
    and skips websites created after the period;
  - generation locks the row (`SKIP LOCKED`) and writes atomically with a SHA-256 checksum
    verified on download; storage paths are confined;
  - regenerating replaces the file and withdraws client approval;
  - stuck rows are re-sent.
- **Publishing and email:** administrators publish client reports, optionally emailing them to
  the website's recipients with the PDF attached (`attachment_report_id` on the notification
  log). The same generation is never emailed twice.
- **Maintenance log:** a `maintenance_activities` table, the `maintenance.write` permission, and
  an API (no future dates; issue links must be on the same website); entries are visible to the
  client or internal.
- **Branding:** company name, accent colour and footer via `/settings/branding`. Logo upload
  checks PNG/JPEG, ≤ 1 MB and a size limit, then re-encodes it with Pillow (SVG and GIF are
  refused).
- **UI:**
  - Reports page: generate, history with status, download, publish / publish & email /
    withdraw; branding for admins.
  - Website page: reports list and maintenance log.
  - Client view: published reports only.
- **Migration 0005:** maintenance table, report email fields, attachment link, permission sync.
  The Docker image installs `fonts-dejavu-core`, and the `appdata` volume holds the files.

**Verification**
- `pytest`: 232 tests passing (16 new), against two months of 5-minute checks with an outage:
  - Exact uptime (99.73%), previous-month comparison, audit, issues, expiry sources,
    recommendations and summary.
  - Client omissions; missing data stated as missing.
  - PDF text extraction: every section, page numbers, footer and branding; escaped markup; the
    logo embedded as an image.
  - Generation, conflict and regeneration (approval withdrawn, file replaced).
  - Access matrix: client sees approved client reports only; technical reports never reach
    clients; unassigned developer → 404; only admins publish.
  - Checksum tampering → 409; email attachment and de-duplication; scheduler idempotency and
    exclusions.
  - Concurrent generation (second worker skips); path traversal; maintenance and branding
    validation; the unreachable-site wording.
- **Live run** with the real worker and SMTP server:
  - A developer logged two maintenance entries (one internal). The admin set branding and a
    logo, generated both reports in the browser, downloaded the technical PDF, and used
    "publish & email".
  - The SMTP server received one email with `www.example.com-2026-11-client-report.pdf`
    attached.
  - The client saw only the client report. The internal entry appeared only in the technical
    PDF.
  - The scheduler correctly created 0 October reports (all websites were created in November).
  - Reviewing the live PDFs led to the wording fixes above, verified by regenerating.
