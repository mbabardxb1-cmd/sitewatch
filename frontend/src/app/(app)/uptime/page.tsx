"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, HealthCell, NoData } from "@/components/ui";
import { api, fmtCoverage, fmtDate, fmtDuration, fmtPct, type Incident, type UptimeOverviewRow, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function UptimePage() {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [sites, setSites] = useState<Website[]>([]);
  const [rows, setRows] = useState<Record<string, UptimeOverviewRow>>({});
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [state, setState] = useState<"open" | "all">("open");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Website[]>("/websites?status=all").then((w) => setSites(w.filter((s) => s.status !== "archived")))
      .catch((e) => setError(e.message));
    api<UptimeOverviewRow[]>("/uptime/overview").then((r) => setRows(Object.fromEntries(r.map((x) => [x.website_id, x]))))
      .catch((e) => setError(e.message));
    api<Incident[]>(`/incidents?state=${state}&limit=100`).then(setIncidents).catch((e) => setError(e.message));
  }, [state]);
  useEffect(load, [load]);

  async function ack(id: string) {
    try {
      await api(`/incidents/${id}/acknowledge`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  const pct = (v: number | null | undefined) => fmtPct(v) ?? <NoData>No data</NoData>;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Uptime Monitoring</h1>
      <ErrorNote message={error} />

      <Card title={state === "open" ? "Active incidents" : "All incidents"} actions={
        <select className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={state} aria-label="Incident filter"
                onChange={(e) => setState(e.target.value as "open" | "all")}>
          <option value="open">Active only</option><option value="all">All</option>
        </select>}>
        {incidents.length === 0 ? (
          <p className="text-sm text-slate-500">{state === "open" ? "No active incidents." : "No incidents recorded."}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Started</th><th className="pr-4">Duration</th>
                  <th className="pr-4">Status</th>{!isClient && <th className="pr-4">Cause</th>}<th /></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {incidents.map((i) => (
                  <tr key={i.id} className="align-top">
                    <td className="py-2 pr-4"><Link className="font-medium hover:underline" href={`/websites/${i.website_id}`}>{i.website_name}</Link></td>
                    <td className="pr-4">{fmtDate(i.started_at)}</td>
                    <td className="pr-4 tabular-nums">
                      {i.ended_at ? fmtDuration(i.duration_s)
                        : <span className="font-medium text-red-700">Ongoing · {fmtDuration(Math.floor((Date.now() - new Date(i.started_at).getTime()) / 1000))}</span>}
                    </td>
                    <td className="pr-4 capitalize">
                      {i.status}
                      {i.acknowledged_by_email && <div className="text-xs normal-case text-slate-500">by {i.acknowledged_by_email}</div>}
                    </td>
                    {!isClient && <td className="pr-4">{i.cause_summary}</td>}
                    <td className="text-right">
                      {can("incident.manage") && i.status === "open" && <Button variant="secondary" onClick={() => ack(i.id)}>Acknowledge</Button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Websites">
        {sites.length === 0 ? <p className="text-sm text-slate-500">No websites.</p> : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Status</th><th className="pr-4 text-right">24 h</th>
                  <th className="hidden pr-4 text-right sm:table-cell">7 days</th><th className="hidden pr-4 text-right sm:table-cell">30 days</th>
                  {!isClient && <th className="hidden pr-4 text-right md:table-cell">Avg response</th>}
                  <th className="hidden pr-4 md:table-cell">Last check</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sites.map((w) => {
                  const r = rows[w.id];
                  return (
                    <tr key={w.id} className="align-top">
                      <td className="py-2 pr-4"><Link href={`/websites/${w.id}`} className="font-medium hover:underline">{w.name}</Link>
                        <div className="break-all text-xs text-slate-500">{w.base_url}</div></td>
                      <td className="py-2 pr-4"><HealthCell status={w.health} reason={w.health_reason} /></td>
                      <td className="py-2 pr-4 text-right tabular-nums">{pct(r?.day)}</td>
                      <td className="hidden py-2 pr-4 text-right tabular-nums sm:table-cell">{pct(r?.week)}</td>
                      <td className="hidden py-2 pr-4 text-right tabular-nums sm:table-cell">
                        {pct(r?.month)}
                        {r && r.month !== null && r.month_coverage_pct < 90 && (
                          <div className="text-xs text-amber-800">Limited data: {fmtCoverage(r.month_coverage_pct)} monitored</div>
                        )}
                      </td>
                      {!isClient && <td className="hidden py-2 pr-4 text-right tabular-nums md:table-cell">
                        {r?.avg_response_ms_24h != null ? `${r.avg_response_ms_24h} ms` : <NoData>—</NoData>}</td>}
                      <td className="hidden py-2 pr-4 text-slate-600 md:table-cell">{fmtDate(w.last_checked_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500">
          Uptime is time-weighted from recorded checks. Time without checks is excluded and never counted as uptime.
        </p>
      </Card>
    </div>
  );
}
