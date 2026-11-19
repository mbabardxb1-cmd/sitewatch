"use client";

import { Activity, CheckCircle2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, HealthCell, NoData, Notice, PageHeader } from "@/components/ui";
import { api, fmtCoverage, fmtDate, fmtDuration, fmtPct, type Incident, type UptimeOverviewRow, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** Plain-language hint for the most common causes, shown when resolving. */
function causeHint(cause = ""): string | null {
  if (/\b403\b/.test(cause)) return "HTTP 403 usually means the website's firewall (Cloudflare, Imunify360, Wordfence…) is blocking the monitor, not that the site is down for visitors. Ask the host to allow the SiteWatch server IP, or add 403 to this website's expected status codes. Otherwise a new incident opens at the next check.";
  if (/DNS/i.test(cause)) return "The domain name does not resolve: the domain may have expired or its DNS records were removed. If the website is retired or moved, pause or archive it.";
  if (/\b50[0-9]\b|timed out|timeout/i.test(cause)) return "Server errors and timeouts on shared hosting often come from resource or bandwidth limits. Check the hosting account before resolving.";
  return null;
}

function ResolveDialog({ incident, canExclude, onClose, onDone }: {
  incident: Incident; canExclude: boolean; onClose: () => void; onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const [include, setInclude] = useState(true);
  const [pause, setPause] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hint = causeHint(incident.cause_summary);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/incidents/${incident.id}/resolve`, { method: "POST", body: { note: note || null, include_in_report: include, pause_monitoring: pause } });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Resolve incident">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <form onSubmit={submit} className="relative w-full max-w-lg space-y-4 rounded-xl bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Resolve incident</h2>
            <p className="text-sm text-slate-500">{incident.website_name} · {incident.cause_summary}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100" aria-label="Close"><X size={18} /></button>
        </div>
        <ErrorNote message={error} />
        {hint && <Notice tone="warning">{hint}</Notice>}
        <label className="block space-y-1.5 text-sm">
          <span className="font-medium text-slate-700">Note (optional, staff only)</span>
          <textarea className="block w-full rounded-lg border-0 px-3 py-2 text-sm shadow-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600"
                    rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder="e.g. Host whitelisted the monitor; site was up for visitors" />
        </label>
        <fieldset className="space-y-2 rounded-lg border border-slate-200 p-3">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-slate-500">Reports</legend>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name="inc" className="mt-0.5" checked={include} onChange={() => setInclude(true)} />
            <span><span className="font-medium text-slate-900">Include in reports</span>
              <span className="block text-slate-500">A real outage: it counts as downtime and is listed in the monthly report.</span></span>
          </label>
          <label className={`flex items-start gap-2 text-sm ${canExclude ? "" : "opacity-50"}`}>
            <input type="radio" name="inc" className="mt-0.5" checked={!include} disabled={!canExclude} onChange={() => setInclude(false)} />
            <span><span className="font-medium text-slate-900">Exclude from reports</span>
              <span className="block text-slate-500">
                {canExclude ? "A false alarm: not counted as downtime and not shown to the client." : "Only administrators can exclude incidents."}
              </span></span>
          </label>
        </fieldset>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-600" checked={pause} onChange={(e) => setPause(e.target.checked)} />
          <span><span className="font-medium text-slate-900">Also pause monitoring of this website</span>
            <span className="block text-slate-500">For a retired or moved website. Otherwise, if it is still failing, a new incident opens at the next check.</span></span>
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy}><CheckCircle2 size={16} />{busy ? "Resolving…" : "Resolve"}</Button>
        </div>
      </form>
    </div>
  );
}

export default function UptimePage() {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [sites, setSites] = useState<Website[]>([]);
  const [rows, setRows] = useState<Record<string, UptimeOverviewRow>>({});
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [state, setState] = useState<"open" | "all">("open");
  const [resolving, setResolving] = useState<Incident | null>(null);
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

  async function setInReport(i: Incident, include: boolean) {
    setError(null);
    try {
      await api(`/incidents/${i.id}/report`, { method: "PUT", body: { include_in_report: include } });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  const canExclude = can("report.approve");
  const pct = (v: number | null | undefined) => fmtPct(v) ?? <NoData>No data</NoData>;

  return (
    <div className="space-y-6">
      <PageHeader title="Uptime Monitoring" icon={<Activity size={20} />} description="Outages are confirmed by retries before an incident is opened. They close automatically after a successful check, or when your team resolves them." />
      <ErrorNote message={error} />
      {resolving && <ResolveDialog incident={resolving} canExclude={canExclude} onClose={() => setResolving(null)}
                                   onDone={() => { setResolving(null); load(); }} />}

      <Card title={state === "open" ? "Active incidents" : "All incidents"} actions={
        <select className="rounded-lg border-0 py-1.5 pl-2.5 pr-8 text-sm shadow-sm ring-1 ring-inset ring-slate-300" value={state} aria-label="Incident filter"
                onChange={(e) => setState(e.target.value as "open" | "all")}>
          <option value="open">Active only</option><option value="all">All</option>
        </select>}>
        {incidents.length === 0 ? (
          <p className="text-sm text-slate-500">{state === "open" ? "No active incidents." : "No incidents recorded."}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="sw-table">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Started</th><th className="pr-4">Duration</th>
                  <th className="pr-4">Status</th>{!isClient && <th className="pr-4">Cause</th>}
                  {!isClient && <th className="pr-4">In reports</th>}<th /></tr>
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
                      {i.status === "resolved" && i.resolution === "manual" ? "Resolved manually" : i.status}
                      {i.status === "resolved" && i.resolution === "manual" && i.resolved_by_email
                        ? <div className="text-xs normal-case text-slate-500">by {i.resolved_by_email}</div>
                        : i.acknowledged_by_email && <div className="text-xs normal-case text-slate-500">by {i.acknowledged_by_email}</div>}
                      {i.resolution_note && <div className="max-w-xs text-xs normal-case text-slate-500">“{i.resolution_note}”</div>}
                      {i.website_status === "paused" && <div className="text-xs normal-case text-amber-700">Monitoring paused</div>}
                    </td>
                    {!isClient && <td className="pr-4">{i.cause_summary}</td>}
                    {!isClient && (
                      <td className="pr-4">
                        <label className={`inline-flex items-center gap-2 text-sm ${canExclude ? "cursor-pointer" : ""}`}
                               title={canExclude ? "Count as downtime and list in reports" : "Only administrators can change this"}>
                          <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-brand-600" checked={i.include_in_report ?? true}
                                 disabled={!canExclude} onChange={(e) => setInReport(i, e.target.checked)} />
                          <span className={i.include_in_report === false ? "text-slate-400" : "text-slate-700"}>
                            {i.include_in_report === false ? "Excluded" : "Included"}
                          </span>
                        </label>
                      </td>
                    )}
                    <td className="text-right">
                      <div className="flex justify-end gap-2">
                        {can("incident.manage") && i.status === "open" && <Button size="sm" variant="secondary" onClick={() => ack(i.id)}>Acknowledge</Button>}
                        {can("incident.manage") && !i.ended_at && (
                          <Button size="sm" onClick={() => setResolving(i)}><CheckCircle2 size={14} />Resolve</Button>
                        )}
                      </div>
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
            <table className="sw-table">
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
