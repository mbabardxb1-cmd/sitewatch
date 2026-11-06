"use client";

import { useCallback, useEffect, useState } from "react";
import { ResponseChart } from "@/components/ResponseChart";
import { Button, Card, ErrorNote, NoData } from "@/components/ui";
import {
  api, fmtCoverage, fmtDate, fmtDuration, fmtPct,
  type Incident, type ResponseBucket, type UptimeCheck, type UptimePeriod, type UptimeSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

const PERIODS: { key: keyof Omit<UptimeSummary, "website_id" | "method">; label: string }[] = [
  { key: "day", label: "Last 24 hours" },
  { key: "week", label: "Last 7 days" },
  { key: "month", label: "Last 30 days" },
  { key: "calendar_month", label: "This month" },
];

function UptimeTile({ label, p }: { label: string; p: UptimePeriod }) {
  const pct = fmtPct(p.uptime_pct);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{pct ?? <NoData />}</div>
      <div className="mt-1 space-y-0.5 text-xs text-slate-500">
        {p.monitored_s > 0 ? (
          <>
            <div>{p.incidents} incident{p.incidents === 1 ? "" : "s"} · {fmtDuration(p.downtime_s)} down</div>
            <div>Based on {fmtDuration(p.monitored_s)} of monitoring ({fmtCoverage(p.coverage_pct)}, {p.checks} checks)</div>
            {p.coverage_pct < 90 && <div className="font-medium text-amber-800">Limited data for this period</div>}
          </>
        ) : (
          <div>No checks recorded in this period</div>
        )}
      </div>
    </div>
  );
}

export function MonitoringPanel({ websiteId, degradedMs, archived }: { websiteId: string; degradedMs?: number; archived: boolean }) {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [uptime, setUptime] = useState<UptimeSummary | null>(null);
  const [hours, setHours] = useState(24);
  const [series, setSeries] = useState<ResponseBucket[]>([]);
  const [checks, setChecks] = useState<UptimeCheck[]>([]);
  const [onlyFailures, setOnlyFailures] = useState(false);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<UptimeSummary>(`/websites/${websiteId}/uptime`).then(setUptime).catch((e) => setError(e.message));
    api<Incident[]>(`/incidents?website_id=${websiteId}&limit=20`).then(setIncidents).catch(() => undefined);
    if (can("uptime.read_detailed")) {
      api<UptimeCheck[]>(`/websites/${websiteId}/checks?limit=50${onlyFailures ? "&only_failures=true" : ""}`)
        .then(setChecks).catch(() => undefined);
    }
  }, [websiteId, can, onlyFailures]);
  useEffect(load, [load]);

  useEffect(() => {
    api<ResponseBucket[]>(`/websites/${websiteId}/response-times?hours=${hours}`).then(setSeries).catch(() => undefined);
  }, [websiteId, hours]);

  async function checkNow() {
    setError(null);
    setNotice(null);
    try {
      await api(`/websites/${websiteId}/check-now`, { method: "POST" });
      setNotice("Check queued. Results appear below within a few seconds.");
      setTimeout(load, 4000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <Card title="Uptime" actions={can("uptime.check_now") && !archived ? <Button variant="secondary" onClick={checkNow}>Run check now</Button> : undefined}>
        <ErrorNote message={error} />
        {notice && <p className="mb-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm text-sky-900">{notice}</p>}
        {uptime && (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {PERIODS.map((p) => <UptimeTile key={p.key} label={p.label} p={uptime[p.key]} />)}
            </div>
            <p className="mt-2 text-xs text-slate-500">{uptime.method}</p>
          </>
        )}
      </Card>

      <Card title="Average response time (hourly)" actions={
        <select className="rounded-lg border-0 py-1.5 pl-2.5 pr-8 text-sm shadow-sm ring-1 ring-inset ring-slate-300" value={hours} onChange={(e) => setHours(Number(e.target.value))}
                aria-label="Time range">
          <option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option>
        </select>}>
        <ResponseChart data={series} hours={hours} thresholdMs={isClient ? undefined : degradedMs} />
      </Card>

      <Card title="Incidents">
        {incidents.length === 0 ? <p className="text-sm text-slate-500">No outages recorded.</p> : (
          <div className="overflow-x-auto">
            <table className="sw-table">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Started</th><th className="pr-4">Ended</th><th className="pr-4">Duration</th>
                  <th className="pr-4">Status</th>{!isClient && <th className="pr-4">Cause</th>}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {incidents.map((i) => (
                  <tr key={i.id}>
                    <td className="py-2 pr-4">{fmtDate(i.started_at)}</td>
                    <td className="pr-4">{i.ended_at ? fmtDate(i.ended_at) : <span className="font-medium text-red-700">Ongoing</span>}</td>
                    <td className="pr-4 tabular-nums">{i.ended_at ? fmtDuration(i.duration_s) : "—"}</td>
                    <td className="pr-4 capitalize">{i.status}</td>
                    {!isClient && <td className="pr-4">{i.cause_summary}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {can("uptime.read_detailed") && (
        <Card title="Recent checks" actions={
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={onlyFailures} onChange={(e) => setOnlyFailures(e.target.checked)} /> Failures only
          </label>}>
          {checks.length === 0 ? <p className="text-sm text-slate-500">No checks recorded yet.</p> : (
            <div className="max-h-96 overflow-auto">
              <table className="sw-table">
                <thead className="sticky top-0 bg-white text-xs uppercase text-slate-500">
                  <tr><th className="py-2 pr-4">Time</th><th className="pr-4">Result</th><th className="pr-4">HTTP</th>
                    <th className="pr-4">Response</th><th className="pr-4">Detail</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {checks.map((c) => (
                    <tr key={c.id} className="align-top">
                      <td className="py-1.5 pr-4 whitespace-nowrap">{fmtDate(c.checked_at)}</td>
                      <td className="pr-4 whitespace-nowrap">
                        <span className={c.ok ? (c.degraded ? "text-amber-700" : "text-emerald-700") : "font-medium text-red-700"}>
                          {c.ok ? (c.degraded ? "Slow" : "OK") : "Failed"}
                        </span>
                        {c.is_retry && <span className="ml-1 text-xs text-slate-500">retry {c.attempt}</span>}
                        {c.is_manual && <span className="ml-1 text-xs text-slate-500">manual</span>}
                      </td>
                      <td className="pr-4 tabular-nums">{c.http_status ?? "—"}</td>
                      <td className="pr-4 tabular-nums">{c.response_ms !== null ? `${c.response_ms} ms` : "—"}</td>
                      <td className="pr-4 text-xs text-slate-600">
                        {c.error_type && <span className="font-mono">{c.error_type}</span>} {c.error_detail}
                        {c.ok && c.final_url && <span className="break-all">{c.final_url}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
