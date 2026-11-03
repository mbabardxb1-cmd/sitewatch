# Operations runbook

Day-to-day tasks for whoever runs SiteWatch. All commands are run in the installation directory.

## Daily / automatic
| What | When | Where it shows |
|---|---|---|
| Uptime checks | Every 1–5 min per website | Uptime Monitoring, dashboards |
| Expiry refresh (domain, SSL, hosting APIs) | Hourly (each item has its own interval) | Domain & Hosting, SSL Certificates |
| Expiry alerts | Hourly; one email per threshold per expiry date | Notifications → Delivery log |
| Monthly audits | Hourly check; runs on each website's audit day | Technical Audits |
| Monthly reports | From the configured report day, for the previous month | Reports (client reports await publication) |
| Data retention | Daily at 03:17 UTC | Settings → retention values |
| Backups | Your cron job (see DEPLOYMENT.md §5) | `backups/` |

## Monthly checklist
1. **Reports → "Awaiting approval":** review each client report and use **Publish** or
   **Publish & email**.
2. **Issues:** move fixed items to "Fixed – awaiting verification"; the next audit confirms or
   re-opens them.
3. **Domain & Hosting:** update any *Manual entry* renewal dates after renewing.
4. **Restore test:** restore last night's backup on a spare machine at least once a quarter.

## Common tasks

**Check overall health**
```bash
docker compose ps
```
```bash
curl -s https://<SITE_DOMAIN>/api/readyz
```

**Follow logs** (JSON; filter by request ID from an error page or the `X-Request-ID` header)
```bash
docker compose logs -f --tail 100 api worker worker-audit beat
```

**Restart background processing**
```bash
docker compose restart worker worker-audit beat
```

**Reset a locked-out user:** Users & Permissions → Activate (clears the lockout). Users change
their own password via the API (`POST /api/v1/auth/change-password`); an administrator can set a
new one with `PATCH /api/v1/users/{id}` (`{"password": "..."}`), which also signs the user out
everywhere. (There is no password-reset email flow yet.)

**Create another administrator**
```bash
docker compose exec api python -m app.cli create-admin --email someone@your-agency.example
```

**Run a manual audit or report:** from the website page (Technical audit → Run audit now) or the
Reports page (Generate).

**Scale workers** (several uptime workers are safe; never run more than one `beat`)
```bash
docker compose up -d --scale worker=3
```

## Incident: "Uptime checks are overdue" or everything shows "No recent checks"
1. `docker compose ps`: is `worker` or `beat` unhealthy or restarting?
2. `docker compose logs --tail 200 beat worker`: look for Redis or database connection errors.
3. `docker compose restart beat worker`. Checks resume within one interval. The gap appears as
   unknown (not as uptime) in statistics and reports.

## Incident: emails not arriving
Notifications → Delivery log shows each attempt with its status and error. Failed deliveries are
retried with backoff; anything stuck as "pending" is re-sent every 10 minutes. Use **Send test**
on the same page after fixing SMTP settings in `.env` (then `docker compose up -d`).

## Incident: disk filling up
- Lower retention in **Settings** (raw uptime checks are the largest table; hourly summaries are
  kept).
- `docker system prune` removes old images after upgrades.
- Check the `backups/` folder and the backup copy job.
