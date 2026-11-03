"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, ErrorNote, HealthCell, NoData, Stat, StatusBadge } from "@/components/ui";
import { api, fmtDate, fmtPct, periodLabel, SEVERITIES, type DashboardSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const NOTICE_STYLE = {
  critical: ["Critical", "bg-red-600"],
  warning: ["Warning", "bg-amber-500"],
  info: ["Info", "bg-sky-500"],
} as const;

function Notices({ data, isClient }: { data: DashboardSummary; isClient: boolean }) {
  return (
    <Card title={isClient ? "Important notices" : "Needs attention"}>
      {data.notices.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing needs attention right now.</p>
      ) : (
        <ul className="space-y-2">
          {data.notices.map((n, i) => {
            const [label, dot] = NOTICE_STYLE[n.level];
            return (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden />
                <span className="w-16 shrink-0 text-xs font-medium uppercase text-slate-500">{label}</span>
                {n.link ? <Link href={n.link} className="hover:underline">{n.text}</Link> : <span>{n.text}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function issuesText(data: DashboardSummary, siteId: string): string {
  const audit = data.audit_state[siteId];
  const counts = data.open_issues[siteId];
  if (!audit || !audit.last_audit_at) return counts ? `No audit yet; ${Object.values(counts).reduce((a, b) => a + (b ?? 0), 0)} open from earlier` : "No technical audit has run yet";
  if (audit.reached_site === false) return "The last audit could not reach the website";
  if (!counts) return "No open issues";
  const serious = (counts.critical ?? 0) + (counts.high ?? 0);
  const total = SEVERITIES.reduce((n, s) => n + (counts[s] ?? 0), 0);
  if (!total) return "No open issues";
  return serious ? `${serious} high-priority, ${total} open in total` : `${total} open (lower priority)`;
}

function ClientDashboard({ data }: { data: DashboardSummary }) {
  const reports = Object.fromEntries(data.latest_reports.map((r) => [r.website_id, r]));
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Your websites" value={data.total_websites} />
        <Stat label="Online now" value={data.online} />
        <Stat label="Offline now" value={data.offline} />
        <Stat label="Uptime (30 days)" value={data.monthly_uptime_pct !== null ? `${data.monthly_uptime_pct.toFixed(2)} %` : <NoData />}
              note={data.monthly_uptime_note} />
      </div>
      <Notices data={data} isClient />
      {data.websites.length === 0 ? (
        <Card><p className="text-sm text-slate-500">No websites have been shared with you yet.</p></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {data.websites.map((w) => {
            const rep = reports[w.id];
            const up = data.uptime_30d[w.id];
            return (
              <section key={w.id} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div>
                  <Link href={`/websites/${w.id}`} className="text-base font-semibold hover:underline">{w.name}</Link>
                  <div className="break-all text-xs text-slate-500">{w.base_url}</div>
                </div>
                <HealthCell status={w.health} reason={w.health_reason} />
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  <div><dt className="text-xs text-slate-500">Uptime, last 30 days</dt><dd className="font-medium tabular-nums">{fmtPct(up) ?? "No data yet"}</dd></div>
                  <div><dt className="text-xs text-slate-500">Last checked</dt><dd>{fmtDate(w.last_checked_at)}</dd></div>
                  <div className="col-span-2"><dt className="text-xs text-slate-500">Issues from technical audits</dt><dd>{issuesText(data, w.id)}</dd></div>
                </dl>
                <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-sm">
                  {rep ? (
                    <>
                      <span>Latest report: <strong>{periodLabel(rep.period)}</strong></span>
                      {rep.overall_level && <StatusBadge status={rep.overall_level} />}
                      <a href={`/api/v1/reports/${rep.report_id}/download`} className="ml-auto rounded-md px-3 py-1.5 font-medium ring-1 ring-slate-300 hover:bg-slate-50">
                        Download PDF
                      </a>
                    </>
                  ) : <span className="text-slate-500">No report has been published yet.</span>}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}

function StaffDashboard({ data }: { data: DashboardSummary }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Websites monitored" value={data.total_websites} note={data.paused ? `${data.paused} paused` : undefined} />
        <Stat label="Online" value={data.online} />
        <Stat label="Offline" value={data.offline} />
        <Stat label="Degraded" value={data.degraded} />
        <Stat label="Avg response (24 h)" value={data.avg_response_ms_24h !== null ? `${data.avg_response_ms_24h} ms` : <NoData />}
              note={data.checks_24h ? `${data.checks_24h} successful checks` : "No checks recorded"} />
        <Stat label="Monthly uptime" value={data.monthly_uptime_pct !== null ? `${data.monthly_uptime_pct.toFixed(2)} %` : <NoData />}
              note={data.monthly_uptime_note} />
        <Stat label="Active incidents" value={data.active_incidents} />
        <Stat label="Not yet checked" value={data.unknown} />
      </div>
      <Notices data={data} isClient={false} />
      <Card title="Websites">
        {data.websites.length === 0 ? <p className="text-sm text-slate-500">No websites assigned to you yet.</p> : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Status</th>
                  <th className="hidden pr-4 text-right sm:table-cell">Uptime 30 d</th>
                  <th className="hidden pr-4 md:table-cell">Open issues</th>
                  <th className="hidden pr-4 lg:table-cell">Client</th><th className="hidden pr-4 lg:table-cell">Last check</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.websites.map((w) => (
                  <tr key={w.id} className="align-top">
                    <td className="py-2 pr-4">
                      <Link href={`/websites/${w.id}`} className="font-medium hover:underline">{w.name}</Link>
                      <div className="break-all text-xs text-slate-500">{w.base_url}</div>
                    </td>
                    <td className="py-2 pr-4"><HealthCell status={w.health} reason={w.health_reason} /></td>
                    <td className="hidden py-2 pr-4 text-right tabular-nums sm:table-cell">{fmtPct(data.uptime_30d[w.id]) ?? <NoData>—</NoData>}</td>
                    <td className="hidden py-2 pr-4 md:table-cell">{issuesText(data, w.id)}</td>
                    <td className="hidden py-2 pr-4 lg:table-cell">{w.client_name}</td>
                    <td className="hidden py-2 pr-4 text-slate-600 lg:table-cell">{fmtDate(w.last_checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

export default function DashboardPage() {
  const { me, can } = useAuth();
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isClient = !!me?.roles.includes("client");

  useEffect(() => {
    const load = () => api<DashboardSummary>("/dashboard/summary").then(setData).catch((e) => setError(e.message));
    load();
    const t = setInterval(load, 60_000); // keep status fresh while the page is open
    return () => clearInterval(t);
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">{isClient ? "Your websites" : "Dashboard"}</h1>
          <p className="text-sm text-slate-500">Signed in as {me?.full_name || me?.email}</p>
        </div>
        {can("website.write") && (
          <Link href="/websites/new" className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">Add website</Link>
        )}
      </div>
      <ErrorNote message={error} />
      {data && (isClient ? <ClientDashboard data={data} /> : <StaffDashboard data={data} />)}
    </div>
  );
}
