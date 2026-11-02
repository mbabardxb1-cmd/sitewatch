# SiteWatch — Architecture & Implementation Proposal

Status: **Draft for approval** · Date: 2026-11-02

This document responds to Section 9 of the requirements: it proposes the architecture,
folder structure, database schema, API surface and phased plan. No application code is
written until the stack and plan are approved.

---

## 1. Architecture

```
                ┌──────────────────────────┐
  Browser ────▶ │  Next.js (TypeScript)    │  SSR + client UI, no secrets, no DB access
                │  Tailwind + shadcn/ui    │
                └───────────┬──────────────┘
                            │ HTTPS, session cookie (HttpOnly, Secure, SameSite=Lax) + CSRF token
                ┌───────────▼──────────────┐
                │  FastAPI  (REST /api/v1) │  ALL authorization enforced here
                └───┬──────────────┬───────┘
                    │              │ enqueue
          SQLAlchemy│        ┌─────▼─────┐      ┌─────────────────────────────┐
                    │        │   Redis   │◀────▶│ Celery workers (queues):     │
             ┌──────▼─────┐  └─────▲─────┘      │  uptime · audit · expiry ·   │
             │ PostgreSQL │◀───────┼────────────│  reports · notify            │
             └────────────┘        │            └─────────────────────────────┘
                              ┌────┴─────┐
                              │ Celery   │  single beat process, ticks only;
                              │ beat     │  real scheduling state lives in Postgres
                              └──────────┘
      Report PDFs → storage volume (local disk, S3-compatible adapter later)
```

**Key design decisions**

| Concern | Decision | Why |
|---|---|---|
| Scheduling | Beat fires a *dispatcher* every 15 s. Dispatcher claims due rows with `SELECT … FOR UPDATE SKIP LOCKED`, advances `next_check_at`, then enqueues. | Monitoring survives restarts, scales to many workers, cannot double-run a check. |
| Monthly jobs (audits/reports) | Rows keyed by a unique `(website_id, period)` constraint; job inserts first, conflicts mean "already done". | Guarantees no duplicate audits/reports even if beat fires twice. |
| Uptime % | Time-weighted from actual check results. Gaps with no data count as **Unknown**, never as "up". | Avoids fabricated uptime. |
| Outage confirmation | Failure → up to N quick retries (configurable) → incident opened → one alert. Recovery after M consecutive successes. | Prevents false alarms and alert spam. |
| Crawler | Custom async crawler on `httpx` + `selectolax` HTML parser (instead of Scrapy). | Scrapy's Twisted reactor conflicts with Celery workers, and it makes per-hop SSRF IP pinning hard. Pluggable Playwright renderer for JS sites (optional). |
| SSRF | Every outbound request: resolve DNS → reject loopback/private/link-local/CGNAT/multicast/reserved/metadata IPs (v4+v6) → **connect to the validated IP** (pinned, defeats DNS rebinding) → redirects followed manually and re-validated each hop. | Required by §6. |
| Honesty of checks | Every audit check records `status ∈ {passed, failed, warning, not_checked, error}`. Reports render "Not checked" explicitly. | Requirement: never claim a check was performed when it wasn't. |
| Tenant isolation | One central `scope` module returns the website IDs a user may see; every repository query joins through it. Unauthorized IDs return **404** (not 403) to avoid enumeration. | Enforced in backend, tested per endpoint. |
| Client data shaping | Separate Pydantic response schemas for client role (no logs, raw errors, notes, credentials). | Field leaks become impossible by construction, not by UI hiding. |
| Notifications | `Notifier` interface; `EmailNotifier` (SMTP) now; Slack/webhook adapters later. Dedup key per (rule, subject, incident). | Extensible; no repeated alerts per incident. |
| Hosting expiry | `HostingProvider` adapter interface + manual entry. Each date stores `source ∈ {manual, api}` and `verified_at`. No data → **Unknown**. | Hosting data is not public. |
| Domain expiry | RDAP via IANA bootstrap → WHOIS (port 43) fallback for TLDs without RDAP → manual entry. Source recorded. | Requirement F3. |
| Secrets | Env vars only; hosting API tokens encrypted at rest (Fernet, key from env); never serialized to clients. | §6. |

---

## 2. Folder structure

```
sitewatch/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory
│   │   ├── config.py               # pydantic-settings (env)
│   │   ├── db.py                   # engine/session
│   │   ├── models/                 # SQLAlchemy models (one file per domain)
│   │   ├── schemas/                # Pydantic: *_internal and *_client variants
│   │   ├── api/v1/                 # routers: auth, users, clients, websites, uptime,
│   │   │                           #   incidents, audits, issues, expiry, reports,
│   │   │                           #   notifications, settings
│   │   ├── auth/                   # sessions, password hashing, permissions, scope
│   │   ├── services/               # business logic (no HTTP)
│   │   ├── netsafe/                # SSRF-safe HTTP client, IP validation
│   │   ├── monitoring/             # uptime check, incident state machine
│   │   ├── crawler/                # crawler, robots, sitemap, audit checks
│   │   ├── expiry/                 # rdap, whois, ssl, hosting adapters
│   │   ├── reports/                # data assembly + ReportLab rendering + charts
│   │   ├── notify/                 # notifier interface, email, templates, dedup
│   │   └── worker/                 # celery app, beat schedule, tasks
│   ├── migrations/                 # Alembic
│   ├── tests/                      # pytest (unit + integration on real Postgres)
│   └── pyproject.toml
├── frontend/
│   ├── src/app/                    # Next.js App Router pages per nav section
│   ├── src/components/             # UI components (status badges, tables, charts)
│   ├── src/lib/api.ts              # typed API client
│   └── package.json
├── deploy/
│   ├── docker-compose.yml          # postgres, redis, api, worker, beat, web, caddy
│   ├── Caddyfile                   # HTTPS (automatic Let's Encrypt)
│   └── backup/                     # pg_dump cron + restore script
├── docs/                           # README, deployment, ops runbook, API, permissions
└── .env.example
```

---

## 3. Database schema (PostgreSQL, UUID PKs, `created_at`/`updated_at` on all tables)

**Identity & access**
- `users` (id, email UNIQUE, password_hash [argon2id], full_name, is_active, client_id FK NULL — set for client users, last_login_at, failed_login_count, locked_until)
- `roles` (id, key UNIQUE: `super_admin` | `developer` | `client`, name)
- `permissions` (id, key UNIQUE e.g. `website.write`, `audit.run`, `issue.note.read`, `report.download`)
- `role_permissions` (role_id, permission_id) PK both
- `user_role_assignments` (user_id, role_id) PK both
- `sessions` (id [random 256-bit, stored hashed], user_id, expires_at, ip, user_agent, revoked_at)

**Tenancy**
- `clients` (id, name, slug UNIQUE, contact_email, is_active, show_expiry_to_client BOOL)
- `websites` (id, client_id FK, name, base_url, hostname, status: active|paused|archived, check_interval_s [60–300], retry_attempts, recovery_threshold, timeout_s, degraded_threshold_ms, expected_status_codes INT[], next_check_at, current_state: up|down|degraded|unknown, audit_day_of_month, crawl_config JSONB {max_depth, max_pages, timeout_s, rate_rps, excluded_patterns[], check_external_links, render_js}, ssl_monitoring BOOL, last_audit_at, archived_at)
  - index (status, next_check_at)
- `website_user_assignments` (website_id, user_id, PK both)

**Uptime**
- `uptime_checks` (id BIGSERIAL, website_id, checked_at, ok BOOL, http_status, response_ms, error_type: timeout|dns|connect|ssl|http_error|blocked, error_detail, final_url, attempt, is_retry) — index (website_id, checked_at DESC); monthly partitioning optional
- `uptime_incidents` (id, website_id, started_at, ended_at NULL, duration_s, status: open|resolved|acknowledged, cause_summary, first_check_id, last_check_id, acknowledged_by)
  - partial unique index: one open incident per website
- `performance_metrics` (website_id, bucket_start [hourly], checks, successes, avg_ms, p95_ms, max_ms) — rollups, PK (website_id, bucket_start)

**Audits**
- `audit_runs` (id, website_id, period [YYYY-MM] NULL for manual, trigger: scheduled|manual, triggered_by, status: queued|running|completed|failed|partial, started_at, finished_at, pages_crawled, config_snapshot JSONB, check_coverage JSONB {check_key: performed|not_checked + reason}, error)
  - UNIQUE (website_id, period) WHERE trigger='scheduled'
- `monitored_urls` (id, website_id, url, url_hash, is_important BOOL, first_seen_at, last_seen_at, last_status) UNIQUE(website_id, url_hash)
- `audit_findings` (id, audit_run_id, website_id, check_key, severity: critical|high|medium|low|info, certainty: confirmed|warning|could_not_check, url, source_url, detail JSONB, fingerprint) — index (website_id, fingerprint)
- `issues` (id, website_id, fingerprint UNIQUE per website, check_key, title, severity, status: open|acknowledged|in_progress|resolved_pending_verification|resolved, first_seen_run_id, last_seen_run_id, resolved_verified_run_id, occurrences, assignee_id)
  - "resolved" only set automatically when a later audit no longer finds it; a developer can mark "resolved — pending verification" only.
- `issue_comments` (id, issue_id, author_id, body, visibility: internal) — never returned to client role

**Expiry**
- `domain_records` (id, website_id, domain, registrar, registrar_url, expires_at, created_on, nameservers TEXT[], source: rdap|whois|registrar_api|manual, raw_ref, last_checked_at, last_error, status: ok|unknown|error)
- `hosting_records` (id, website_id, provider, plan, account_reference, renewal_date, source: manual|api, verified_at, entered_by, provider_integration_id NULL, notes [internal])
- `hosting_integrations` (id, provider_key, display_name, encrypted_credentials, created_by) — never serialized to clients
- `ssl_certificates` (id, website_id, hostname, issuer, subject, san TEXT[], not_before, not_after, valid BOOL, validation_error, checked_at)

**Notifications & reports**
- `notification_rules` (id, website_id NULL = global, client_id NULL, event_type, enabled, recipients TEXT[], channel: email, thresholds JSONB e.g. {days_before:[60,30,14,7]}, min_interval_s)
- `notification_logs` (id, rule_id, event_type, website_id, dedup_key, channel, recipients, status: sent|failed|suppressed, error, sent_at) — UNIQUE(dedup_key, channel)
- `monthly_reports` (id, website_id, period, kind: client|technical, status: generating|ready|failed, approved_for_client BOOL, approved_by, file_path, file_sha256, size_bytes, generated_at, data_snapshot JSONB) — UNIQUE(website_id, period, kind)
- `system_settings` (key PK, value JSONB, updated_by) — retention days, report day, branding/logo, SMTP from-name, etc.
- `audit_log` (actor_id, action, target_type, target_id, ip, at) — security trail of admin actions

---

## 4. API endpoints (`/api/v1`, JSON; all require session except login)

| Area | Endpoints | Min role |
|---|---|---|
| Auth | `POST /auth/login` (rate-limited), `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` | any |
| Users | `GET/POST /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/deactivate`, `PUT /users/{id}/roles` | admin |
| Clients | `GET/POST /clients`, `GET/PATCH /clients/{id}` | admin (dev: read assigned) |
| Websites | `GET /websites`, `POST /websites`, `GET/PATCH /websites/{id}`, `POST /websites/{id}/archive`, `PUT /websites/{id}/assignments`, `PATCH /websites/{id}/crawl-config` | list/get: all scoped · write: admin, dev (if `website.write`) |
| Dashboard | `GET /dashboard/summary` (role-shaped response) | all scoped |
| Uptime | `GET /websites/{id}/checks` (dev/admin), `GET /websites/{id}/uptime?period=day\|week\|month`, `GET /websites/{id}/response-times`, `POST /websites/{id}/check-now` | scoped; raw checks dev+ |
| Incidents | `GET /incidents`, `GET /incidents/{id}`, `POST /incidents/{id}/acknowledge` | scoped; ack dev+ |
| Audits | `GET /websites/{id}/audits`, `POST /websites/{id}/audits` (manual run), `GET /audits/{id}`, `GET /audits/{id}/findings`, `GET /audits/{id}/compare/{other}`, `GET /audits/{id}/export?format=csv\|json` | dev+ (client: summary only) |
| Issues | `GET /issues`, `GET/PATCH /issues/{id}`, `GET/POST /issues/{id}/comments` | client: summarized list, no comments |
| Expiry | `GET /websites/{id}/domain`, `POST /websites/{id}/domain/refresh`, `GET/PUT /websites/{id}/hosting`, `GET /websites/{id}/ssl`, `POST /websites/{id}/ssl/refresh`, `GET /expiry/overview` | client only if `show_expiry_to_client` |
| Integrations | `GET/POST/DELETE /integrations/hosting` | admin |
| Reports | `GET /reports`, `POST /websites/{id}/reports` (manual), `GET /reports/{id}`, `GET /reports/{id}/download`, `POST /reports/{id}/approve` | client: approved+client-kind only |
| Notifications | `GET/POST/PATCH/DELETE /notification-rules`, `GET /notification-logs`, `POST /notifications/test` | admin (dev read) |
| Settings | `GET/PATCH /settings` | admin |
| Health | `GET /healthz`, `GET /readyz` (DB, Redis, worker heartbeat age) | public / internal |

---

## 5. Phased plan (each phase: implement → tests green → docs → your review)

| Phase | Deliverable | Verification |
|---|---|---|
| **1** | Repo skeleton, Docker Compose, config, Alembic migrations for full schema, seed roles/permissions, session auth (argon2id, CSRF, login rate-limit, lockout), permission + scope framework, user mgmt API, Next.js shell with login and nav | pytest: auth flows, rate limit, role matrix; migration up/down |
| **2** | Clients & websites CRUD, assignments, crawl config validation, dashboard summary endpoint + UI (Websites, Dashboard pages) | API tests incl. cross-tenant 404s; UI build passes |
| **3** | SSRF-safe client, uptime worker, dispatcher, retry/confirm logic, incidents, rollups, uptime %, email notifier with dedup, Uptime & Incidents UI | Tests against local test servers (up/down/slow/500/bad SSL), SSRF block tests, "no duplicate alerts" test, beat-twice idempotency test |
| **4** | Crawler (robots, sitemap, depth/page/rate limits, exclusions), all audit checks with coverage tracking, findings → issues with new/resolved/recurring diffing, CSV/JSON export, Audits & Issues UI | Crawl a local fixture site with known defects; assert exact findings + "not_checked" coverage |
| **5** | RDAP/WHOIS domain lookups, SSL checks, hosting manual entry + adapter interface (+ any provider you name), expiry alerts at 60/30/14/7 days | Unit tests with recorded RDAP fixtures; live RDAP smoke test |
| **6** | Report data assembly, ReportLab PDF (logo, charts, tables, page numbers, month-over-month), scheduled + manual generation, storage, approval, authorized download, optional email | Generate sample PDFs from fixture data; text-extract and assert sections & "Not checked" labels |
| **7** | Client dashboard, client-shaped schemas everywhere, full permission test matrix (every endpoint × every role × foreign tenant) | Automated matrix test must be 100% green |
| **8** | Production compose + Caddy HTTPS, worker health/heartbeat, structured logging, Sentry hook (optional), backup/restore scripts (tested), retention job, README + deployment + runbook | Fresh `docker compose up` end-to-end; restore drill |

---

## 6. Third-party credentials / paid services

| Feature | Needs | Required? |
|---|---|---|
| Email alerts & reports | SMTP server or provider (Postmark, SES, SendGrid, Mailgun) | Yes, for notifications (dev uses Mailpit locally) |
| Domain expiry | RDAP is free/public; WHOIS port-43 free but rate-limited, ToS varies by registry | No |
| Registrar APIs (optional) | API keys per registrar (e.g. Namecheap, GoDaddy, Cloudflare) | Optional |
| Hosting renewal | Provider API keys (e.g. WHM/cPanel, DigitalOcean, Hetzner, AWS) — only some expose billing/renewal dates | Optional; manual fallback always available |
| HTTPS for the app | A domain + DNS pointing to the server (Let's Encrypt is free) | Yes, for production |
| Error monitoring | Sentry DSN | Optional |
| JS rendering | Playwright/Chromium in worker image (free, heavier image) | Optional |

---

## 7. Known limitations (stated up front)
- Monitoring is from a **single location**; a regional network problem between our server and a site can look like downtime. Multi-region probes can be added later.
- Hosting expiry is verified only where a provider exposes it via an authorized API; otherwise "Manual" or "Unknown".
- WHOIS data for some TLDs (and privacy-protected domains) may omit expiry; shown as Unknown.
- Crawling JS-rendered sites without the optional Playwright renderer will miss client-side links; the audit records this in coverage.
