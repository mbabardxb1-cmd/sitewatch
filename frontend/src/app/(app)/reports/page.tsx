"use client";

import { useCallback, useEffect, useState } from "react";
import { ReportsTable } from "@/components/ReportsTable";
import { Button, Card, ErrorNote, inputClass } from "@/components/ui";
import { api, lastMonths, periodLabel, type ReportRow, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function ReportsPage() {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [sites, setSites] = useState<Website[]>([]);
  const [filter, setFilter] = useState({ website_id: "", period: "" });
  const [gen, setGen] = useState({ website_id: "", period: lastMonths(2)[1], kind: "both", regenerate: false });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams();
    if (filter.website_id) p.set("website_id", filter.website_id);
    if (filter.period) p.set("period", filter.period);
    api<ReportRow[]>(`/reports?${p}`).then(setRows).catch((e) => setError(e.message));
  }, [filter]);
  useEffect(load, [load]);
  useEffect(() => { api<Website[]>("/websites").then(setSites).catch(() => undefined); }, []);
  useEffect(() => {
    if (!rows.some((r) => r.status === "generating")) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [rows, load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(ok);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Reports</h1>
      <p className="text-sm text-slate-600">
        {isClient
          ? "Monthly maintenance reports for your websites."
          : "Reports are generated automatically each month for the previous month: a technical report for the team and a client report, which clients see only after it is published."}
      </p>
      <ErrorNote message={error} />
      {notice && <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{notice}</p>}

      {can("report.generate") && (
        <Card title="Generate a report">
          <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" onSubmit={(e) => {
            e.preventDefault();
            run(() => api(`/websites/${gen.website_id}/reports`, { method: "POST", body: { period: gen.period, kind: gen.kind, regenerate: gen.regenerate } }),
                "Generation started. The list refreshes automatically.");
          }}>
            <select className={inputClass} required aria-label="Website" value={gen.website_id} onChange={(e) => setGen({ ...gen, website_id: e.target.value })}>
              <option value="">Website…</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select className={inputClass} aria-label="Period" value={gen.period} onChange={(e) => setGen({ ...gen, period: e.target.value })}>
              {lastMonths(13).map((p, i) => <option key={p} value={p}>{periodLabel(p)}{i === 0 ? " (so far)" : ""}</option>)}
            </select>
            <select className={inputClass} aria-label="Report type" value={gen.kind} onChange={(e) => setGen({ ...gen, kind: e.target.value })}>
              <option value="both">Client + technical</option><option value="client">Client</option><option value="technical">Technical</option>
            </select>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={gen.regenerate} onChange={(e) => setGen({ ...gen, regenerate: e.target.checked })} />
              Replace existing (withdraws publication)
            </label>
            <Button type="submit" disabled={!gen.website_id}>Generate</Button>
          </form>
        </Card>
      )}

      <Card title="Report history" actions={
        <div className="flex gap-2">
          <select className="rounded-md border border-slate-300 px-2 py-1 text-sm" aria-label="Filter website" value={filter.website_id}
                  onChange={(e) => setFilter({ ...filter, website_id: e.target.value })}>
            <option value="">All websites</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <select className="rounded-md border border-slate-300 px-2 py-1 text-sm" aria-label="Filter period" value={filter.period}
                  onChange={(e) => setFilter({ ...filter, period: e.target.value })}>
            <option value="">All periods</option>{lastMonths(13).map((p) => <option key={p} value={p}>{periodLabel(p)}</option>)}
          </select>
        </div>}>
        <ReportsTable rows={rows}
          onApprove={(r, email) => run(() => api(`/reports/${r.id}/approve`, { method: "POST", body: { email } }),
                                       email ? "Published and emailed to the website's recipients." : "Published to the client.")}
          onWithdraw={(r) => run(() => api(`/reports/${r.id}/withdraw`, { method: "POST" }), "Withdrawn from the client.")} />
      </Card>

      {can("settings.manage") && (
        <p className="text-xs text-slate-500">Report branding (logo, colours, footer) is managed on the <a className="underline" href="/settings">Settings</a> page.</p>
      )}
    </div>
  );
}
