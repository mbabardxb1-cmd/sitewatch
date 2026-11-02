"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, ErrorNote, HealthCell, NoData, Stat } from "@/components/ui";
import { api, fmtDate, type DashboardSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { me, can } = useAuth();
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isClient = me?.roles.includes("client");

  useEffect(() => {
    api<DashboardSummary>("/dashboard/summary").then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-sm text-slate-500">Signed in as {me?.full_name || me?.email}</p>
        </div>
        {can("website.write") && (
          <Link href="/websites/new" className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">
            Add website
          </Link>
        )}
      </div>
      <ErrorNote message={error} />
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Websites monitored" value={data.total_websites}
                  note={data.paused ? `${data.paused} paused` : undefined} />
            <Stat label="Online" value={data.online} />
            <Stat label="Offline" value={data.offline} />
            <Stat label="Degraded" value={data.degraded} />
            {!isClient && (
              <Stat label="Avg response (24 h)"
                    value={data.avg_response_ms_24h !== null ? `${data.avg_response_ms_24h} ms` : <NoData />}
                    note={data.checks_24h ? `${data.checks_24h} successful checks` : "No checks recorded"} />
            )}
            <Stat label="Monthly uptime"
                  value={data.monthly_uptime_pct !== null ? `${data.monthly_uptime_pct.toFixed(2)} %` : <NoData />}
                  note={data.monthly_uptime_pct === null ? data.monthly_uptime_note : undefined} />
            <Stat label="Active incidents" value={data.active_incidents} />
            <Stat label="Not yet checked" value={data.unknown} />
          </div>

          <Card title="Websites">
            {data.websites.length === 0 ? (
              <p className="text-sm text-slate-500">
                {isClient ? "No websites have been shared with you yet." : "No websites assigned to you yet."}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Status</th>
                      {!isClient && <th className="hidden pr-4 sm:table-cell">Client</th>}<th className="hidden pr-4 sm:table-cell">Last check</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.websites.map((w) => (
                      <tr key={w.id} className="align-top">
                        <td className="py-2 pr-4">
                          <Link href={`/websites/${w.id}`} className="font-medium hover:underline">{w.name}</Link>
                          <div className="break-all text-xs text-slate-500">{w.base_url}</div>
                        </td>
                        <td className="py-2 pr-4"><HealthCell status={w.health} reason={w.health_reason} /></td>
                        {!isClient && <td className="hidden py-2 pr-4 sm:table-cell">{w.client_name}</td>}
                        <td className="hidden py-2 pr-4 text-slate-600 sm:table-cell">{fmtDate(w.last_checked_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
