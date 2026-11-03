"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, HealthCell } from "@/components/ui";
import { ExpiryCard } from "@/components/ExpiryCard";
import { MonitoringPanel } from "@/components/MonitoringPanel";
import { WebsiteAuditCard } from "@/components/WebsiteAuditCard";
import { CrawlConfigForm, MonitoringFields, type MonitoringValues } from "@/components/WebsiteForms";
import { ApiError, api, fmtDate, isStaffView, type UserRow, type Website, type WebsiteStaff } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function pickMonitoring(w: WebsiteStaff): MonitoringValues {
  const { name, base_url, check_interval_s, retry_attempts, recovery_threshold, timeout_s, degraded_threshold_ms,
    expected_status_codes, audit_day_of_month, ssl_monitoring, notification_recipients } = w;
  return { name, base_url, check_interval_s, retry_attempts, recovery_threshold, timeout_s, degraded_threshold_ms,
    expected_status_codes, audit_day_of_month, ssl_monitoring, notification_recipients };
}

export default function WebsiteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const [site, setSite] = useState<Website | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Website>(`/websites/${id}`).then(setSite).catch((e) => {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
      else setError(e.message);
    });
  }, [id]);
  useEffect(load, [load]);

  async function act(path: string, body?: unknown, method = "POST") {
    setError(null);
    try {
      setSite(await api<Website>(`/websites/${id}${path}`, { method, body }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  if (notFound) return <ErrorNote message="Website not found." />;
  if (!site) return <p className="text-sm text-slate-500">{error ?? "Loading…"}</p>;

  const staff = isStaffView(site) ? site : null;
  const writable = can("website.write") && site.status !== "archived";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/websites" className="text-sm text-slate-500 hover:underline">← Websites</Link>
          <h1 className="text-xl font-semibold">{site.name}</h1>
          <a href={site.base_url} target="_blank" rel="noopener noreferrer" className="text-sm text-slate-600 hover:underline">
            {site.base_url}
          </a>
          <div className="text-sm text-slate-500">{site.client_name}</div>
        </div>
        {can("website.write") && (
          <div className="flex flex-wrap gap-2">
            {site.status === "active" && <Button variant="secondary" onClick={() => act("", { status: "paused" }, "PATCH")}>Pause monitoring</Button>}
            {site.status === "paused" && <Button variant="secondary" onClick={() => act("", { status: "active" }, "PATCH")}>Resume monitoring</Button>}
            {site.status !== "archived" ? (
              <Button variant="danger" onClick={() => confirm("Archive this website? Monitoring stops; history is kept.") && act("/archive")}>
                Archive
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => act("/restore")}>Restore</Button>
            )}
          </div>
        )}
      </div>
      <ErrorNote message={error} />

      <Card title="Status">
        <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-slate-500">Health</dt><dd className="mt-1"><HealthCell status={site.health} reason={site.health_reason} /></dd></div>
          <div><dt className="text-slate-500">Monitoring</dt><dd className="mt-1 capitalize">{site.status}</dd></div>
          <div><dt className="text-slate-500">Last check</dt><dd className="mt-1">{fmtDate(site.last_checked_at)}</dd></div>
          <div><dt className="text-slate-500">Last audit</dt><dd className="mt-1">{fmtDate(site.last_audit_at)}</dd></div>
          {staff && <>
            <div><dt className="text-slate-500">Next scheduled check</dt><dd className="mt-1">{fmtDate(staff.next_check_at, "Not scheduled")}</dd></div>
            <div><dt className="text-slate-500">Added</dt><dd className="mt-1">{fmtDate(staff.created_at)}</dd></div>
            {staff.archived_at && <div><dt className="text-slate-500">Archived</dt><dd className="mt-1">{fmtDate(staff.archived_at)}</dd></div>}
          </>}
        </dl>
      </Card>

      <MonitoringPanel websiteId={site.id} degradedMs={staff?.degraded_threshold_ms} archived={site.status === "archived"} />

      <WebsiteAuditCard websiteId={site.id} auditDay={staff?.audit_day_of_month} archived={site.status === "archived"} />

      <ExpiryCard websiteId={site.id} />

      {staff && <MonitoringCard site={staff} writable={writable} onSaved={setSite} />}
      {staff && (
        <Card title="Crawl configuration">
          <CrawlConfigForm key={staff.id} initial={staff.crawl_config}
                           disabled={!can("website.crawl_config") || site.status === "archived"}
                           onSave={async (c) => setSite(await api<Website>(`/websites/${id}/crawl-config`, { method: "PUT", body: c }))} />
        </Card>
      )}
      {staff && <AssignmentsCard site={staff} editable={can("website.assign")} onSaved={setSite} />}
    </div>
  );
}

function MonitoringCard({ site, writable, onSaved }: { site: WebsiteStaff; writable: boolean; onSaved: (w: Website) => void }) {
  const [v, setV] = useState(pickMonitoring(site));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      onSaved(await api<Website>(`/websites/${site.id}`, { method: "PATCH", body: v }));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <Card title="Monitoring settings">
      <form onSubmit={submit} className="space-y-4">
        <ErrorNote message={error} />
        <MonitoringFields v={v} set={setV} disabled={!writable} />
        {writable && (
          <div className="flex items-center gap-3">
            <Button type="submit">Save settings</Button>
            {saved && <span className="text-sm text-emerald-700">Saved</span>}
          </div>
        )}
      </form>
    </Card>
  );
}

function AssignmentsCard({ site, editable, onSaved }: { site: WebsiteStaff; editable: boolean; onSaved: (w: Website) => void }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [selected, setSelected] = useState(new Set(site.assigned_users.map((u) => u.id)));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (editable) api<UserRow[]>("/users").then(setUsers).catch((e) => setError(e.message));
  }, [editable]);

  // Super admins see every site already; client users may only be linked to their own organization.
  const candidates = users.filter((u) => u.is_active && !u.roles.includes("super_admin")
    && (!u.roles.includes("client") || u.client_id === site.client_id));

  async function save() {
    setError(null);
    try {
      onSaved(await api<Website>(`/websites/${site.id}/assignments`, { method: "PUT", body: { user_ids: [...selected] } }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <Card title="Assigned people">
      <ErrorNote message={error} />
      {!editable ? (
        site.assigned_users.length ? (
          <ul className="text-sm">{site.assigned_users.map((u) => <li key={u.id}>{u.full_name || u.email} <span className="text-slate-500">({u.role})</span></li>)}</ul>
        ) : <p className="text-sm text-slate-500">Nobody is assigned.</p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">Super administrators can access every website and are not listed.</p>
          <div className="grid gap-1 sm:grid-cols-2">
            {candidates.map((u) => (
              <label key={u.id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={selected.has(u.id)} onChange={(e) => {
                  const next = new Set(selected);
                  if (e.target.checked) next.add(u.id); else next.delete(u.id);
                  setSelected(next);
                }} />
                {u.full_name || u.email}
                <span className="text-xs text-slate-500">{u.roles.includes("client") ? "client" : "developer"}</span>
              </label>
            ))}
          </div>
          <Button onClick={save}>Save assignments</Button>
        </div>
      )}
    </Card>
  );
}
